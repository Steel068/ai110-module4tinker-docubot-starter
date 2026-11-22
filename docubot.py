"""
Core DocuBot class responsible for:
- Loading documents from the docs/ folder
- Building a simple retrieval index (Phase 1)
- Retrieving relevant snippets (Phase 1)
- Supporting retrieval only answers
- Supporting RAG answers when paired with Gemini (Phase 2)
"""

import os
import glob
import re

# Common words that appear in almost every question or document. They carry
# no meaning for search, so they are ignored when indexing and scoring.
STOPWORDS = {
    "a", "an", "the", "is", "are", "was", "be", "to", "of", "in", "on", "for",
    "and", "or", "by", "with", "it", "this", "that", "these", "those", "there",
    "any", "do", "does", "i", "me", "my", "we", "you", "your", "how", "what",
    "where", "which", "who", "when", "why", "can", "should", "if", "not",
    "from", "at", "as", "about", "into", "mention", "docs",
    # "return" shows up in nearly every API section ("Returns ..."), so it
    # cannot tell sections apart.
    "return", "returns",
}

# Guardrail: a section only counts as evidence if it contains at least this
# fraction of the question's keywords. Below that, DocuBot refuses.
MIN_KEYWORD_COVERAGE = 0.5


def stem(word):
    """
    Very small stemmer so word forms match each other, e.g.
    generated / generate / generation -> "generat", users -> "user".
    Strips the first matching suffix, but never shortens a word below 3 letters.
    """
    for suffix in ("ion", "ing", "ed", "es", "s", "e"):
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)]
    return word


def tokenize(text):
    """
    Lowercase the text, split it into words (punctuation and underscores
    act as separators), drop stopwords, and stem what is left.
    Example: "Where is the auth token generated?" -> ["auth", "token", "generat"]
    """
    words = re.findall(r"[a-z0-9]+", text.lower())
    return [stem(w) for w in words if w not in STOPWORDS]


def split_into_sections(text):
    """
    Split a markdown document into sections: every heading line (a line
    beginning with "#") starts a new section. Sections that are only a
    heading with no body text (e.g. "## User Data Endpoints") are dropped,
    since they hold no answer on their own.
    """
    sections = []
    current = []

    for line in text.splitlines():
        if line.startswith("#") and current:
            sections.append(current)
            current = []
        current.append(line)
    sections.append(current)

    result = []
    for lines in sections:
        has_body = any(l.strip() and not l.startswith("#") for l in lines)
        if has_body:
            result.append("\n".join(lines).strip())
    return result


def heading_words(text):
    """
    Keywords from a section's own heading. Only "##" and deeper count:
    a "#" title describes the whole file, not this particular section.
    """
    words = []
    for line in text.splitlines():
        if line.startswith("##"):
            words.extend(tokenize(line))
    return set(words)


class DocuBot:
    def __init__(self, docs_folder="docs", llm_client=None):
        """
        docs_folder: directory containing project documentation files
        llm_client: optional Gemini client for LLM based answers
        """
        self.docs_folder = docs_folder
        self.llm_client = llm_client

        # Load documents into memory
        self.documents = self.load_documents()  # List of (filename, text)

        # Retrieval works on sections, not whole files (Phase 3 refinement)
        self.sections = self.build_sections(self.documents)  # List of (filename, section_text)

        # Inverted index: word -> set of section numbers (positions in self.sections)
        self.index = self.build_index(self.sections)

    # -----------------------------------------------------------
    # Document Loading
    # -----------------------------------------------------------

    def load_documents(self):
        """
        Loads all .md and .txt files inside docs_folder.
        Returns a list of tuples: (filename, text)
        """
        docs = []
        pattern = os.path.join(self.docs_folder, "*.*")
        for path in glob.glob(pattern):
            if path.endswith(".md") or path.endswith(".txt"):
                with open(path, "r", encoding="utf8") as f:
                    text = f.read()
                filename = os.path.basename(path)
                docs.append((filename, text))
        return docs

    def build_sections(self, documents):
        """
        Breaks every document into heading-based sections.
        Returns a list of tuples: (filename, section_text)
        """
        sections = []
        for filename, text in documents:
            for section_text in split_into_sections(text):
                sections.append((filename, section_text))
        return sections

    # -----------------------------------------------------------
    # Index Construction (Phase 1)
    # -----------------------------------------------------------

    def build_index(self, sections):
        """
        Build an inverted index mapping each (stemmed, lowercase) word to the
        sections it appears in. Sections are identified by their position
        in the list, because one file contains many sections.

        Example structure:
        {
            "token": {3, 4, 12},     # section numbers
            "databas": {20, 21}
        }
        """
        index = {}
        for section_id, (_, text) in enumerate(sections):
            for word in tokenize(text):
                index.setdefault(word, set()).add(section_id)
        return index

    # -----------------------------------------------------------
    # Scoring and Retrieval (Phase 1)
    # -----------------------------------------------------------

    def score_document(self, query, text):
        """
        Relevance score =
            (distinct query keywords found anywhere in the text)
          + (distinct query keywords found in the section's own heading)

        The heading bonus means a section titled "Token Generation" beats a
        section that only mentions tokens in passing. Stopwords are already
        removed by tokenize(), so only meaningful words count.
        """
        query_words = set(tokenize(query))
        text_words = set(tokenize(text))
        body_matches = len(query_words & text_words)
        heading_matches = len(query_words & heading_words(text))
        return body_matches + heading_matches

    def has_enough_evidence(self, query, text):
        """
        Guardrail: does this text actually support the query?
        It must contain at least MIN_KEYWORD_COVERAGE of the query's keywords.
        (Uses plain coverage, not the heading-boosted score, so a heading
        match cannot sneak a weak section past the guardrail.)
        A query with no keywords at all (only stopwords) never has evidence.
        """
        query_words = set(tokenize(query))
        if not query_words:
            return False
        matched = query_words & set(tokenize(text))
        return len(matched) / len(query_words) >= MIN_KEYWORD_COVERAGE

    def retrieve(self, query, top_k=3):
        """
        Select the top_k most relevant sections for the query.

        1. Use the index to find candidate sections sharing a keyword.
        2. Score each candidate.
        3. Drop candidates that fail the evidence guardrail.
        4. Sort by score (highest first); shorter sections win ties
           because they are more focused.

        Returns a list of (filename, section_text). An empty list means
        "no meaningful evidence", which makes the answer modes refuse.
        """
        candidate_ids = set()
        for word in tokenize(query):
            candidate_ids |= self.index.get(word, set())

        results = []
        for section_id in candidate_ids:
            filename, text = self.sections[section_id]
            if not self.has_enough_evidence(query, text):
                continue
            score = self.score_document(query, text)
            results.append((score, len(text), filename, text))

        results.sort(key=lambda r: (-r[0], r[1]))
        return [(filename, text) for _, _, filename, text in results[:top_k]]

    # -----------------------------------------------------------
    # Answering Modes
    # -----------------------------------------------------------

    def answer_retrieval_only(self, query, top_k=3):
        """
        Phase 1 retrieval only mode.
        Returns raw snippets and filenames with no LLM involved.
        """
        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        formatted = []
        for filename, text in snippets:
            formatted.append(f"[{filename}]\n{text}\n")

        return "\n---\n".join(formatted)

    def answer_rag(self, query, top_k=3):
        """
        Phase 2 RAG mode.
        Uses student retrieval to select snippets, then asks Gemini
        to generate an answer using only those snippets.
        """
        if self.llm_client is None:
            raise RuntimeError(
                "RAG mode requires an LLM client. Provide a GeminiClient instance."
            )

        snippets = self.retrieve(query, top_k=top_k)

        if not snippets:
            return "I do not know based on these docs."

        return self.llm_client.answer_from_snippets(query, snippets)

    # -----------------------------------------------------------
    # Bonus Helper: concatenated docs for naive generation mode
    # -----------------------------------------------------------

    def full_corpus_text(self):
        """
        Returns all documents concatenated into a single string.
        This is used in Phase 0 for naive 'generation only' baselines.
        """
        return "\n\n".join(text for _, text in self.documents)
