# DocuBot Model Card

This model card is a short reflection on your DocuBot system. Fill it out after you have implemented retrieval and experimented with all three modes:

1. Naive LLM over full docs  
2. Retrieval only  
3. RAG (retrieval plus LLM)

Use clear, honest descriptions. It is fine if your system is imperfect.

---

## 1. System Overview

**What is DocuBot trying to do?**  
Describe the overall goal in 2 to 3 sentences.

> DocuBot answers developer questions about a codebase using the project's own documentation (`docs/`). The goal is answers that are grounded in what the docs actually say, with a clear refusal when the docs do not cover the question, instead of a confident guess.

**What inputs does DocuBot take?**  
For example: user question, docs in folder, environment variables.

> - A developer question (typed in, or the 8 built-in `SAMPLE_QUERIES` in `dataset.py`)
> - The `.md` / `.txt` files in `docs/` (API_REFERENCE, AUTH, DATABASE, SETUP)
> - `GEMINI_API_KEY` from `.env` (only needed for modes 1 and 3)
> - The mode choice (1, 2 or 3)

**What outputs does DocuBot produce?**

> - Mode 1: a free-form Gemini answer.
> - Mode 2: up to 3 raw doc sections, each labelled with its filename, or `I do not know based on these docs.`
> - Mode 3: a Gemini answer written only from the retrieved sections, naming the files it used, or a refusal.

---

## 2. Retrieval Design

**How does your retrieval system work?**  
Describe your choices for indexing and scoring.

- How do you turn documents into an index?
- How do you score relevance for a query?
- How do you choose top snippets?

> **Unit of retrieval: sections, not files.** Each document is split at markdown headings (`##`, `###`, …), so `## Token Generation` and `### GET /api/users` become separate snippets (36 sections in total). Sections that are only a heading with no body are dropped.
>
> **Tokenizing:** lowercase, split on anything that is not a letter or digit (so `generate_access_token` gives `generate`, `access`, `token`), remove stopwords (`the`, `is`, `how`, `which`, `return`, …), then a tiny suffix-stripping stemmer (`generated` / `generate` / `generation` → `generat`, `users` → `user`).
>
> **Index:** an inverted index `word → set of section numbers`.
>
> **Score:** (number of distinct query keywords in the section) + (number of distinct query keywords in the section's own `##`+ heading). The heading bonus makes "Token Generation" beat a section that only mentions tokens in passing.
>
> **Choosing snippets:**
> 1. Use the index to find candidate sections.
> 2. Drop any that fail the evidence guardrail (below).
> 3. Sort by score; ties go to the shorter, more focused section.
> 4. Return the top 3.
>
> **Evaluation:** `evaluation.py` hit rate = 0.88 (7/8). The only "miss" is the payment question, which correctly returns nothing (the evaluator counts any query with no expected file as a miss), so 0.88 is the maximum here.

**What tradeoffs did you make?**  
For example: speed vs precision, simplicity vs accuracy.

> - **Simplicity over accuracy.** Exact word matching with a hand-made stemmer is easy to reason about line by line, but it misses synonyms ("port" vs `localhost:5000`) and some word forms ("running" → `runn` doesn't match "run").
> - **Precision over context.** Section-sized snippets are focused (≈100–500 characters instead of ≈3,000), but an answer that spans two sections can lose half its context.
> - **Hand-tuned rules.** The stopword list (including "return") and the 50% coverage threshold were tuned on these 4 docs and 8 queries. They may not carry over to a different doc set.
> - **Refusing too much rather than guessing.** The guardrail sometimes refuses answerable questions (see failure case 1), which I consider safer than answering unanswerable ones.

**How the design evolved (Part 2 → Part 3):**  
> My first version indexed whole files and counted every shared word. Filler words like "the" and "how" dominated, so "How do I connect to the database?" ranked SETUP.md above DATABASE.md. Every query, including "payment processing", returned 3 whole files of about 3 KB each. Removing stopwords, splitting into sections, adding the heading bonus and adding the coverage guardrail fixed those problems.

---

## 3. Use of the LLM (Gemini)

**When does DocuBot call the LLM and when does it not?**  
Briefly describe how each mode behaves.

- Naive LLM mode:
- Retrieval only mode:
- RAG mode:

> - **Naive LLM mode:** calls Gemini once per question. Note: although the mode is named "over full docs", `naive_answer_over_full_docs` ignores `all_text` and sends only the question. The model never sees our docs, so this mode is really "LLM from general knowledge".
> - **Retrieval only mode:** never calls the LLM. It prints the retrieved sections or refuses.
> - **RAG mode:** runs retrieval first. If retrieval returns nothing, it refuses **without** calling Gemini. That saves an API call and makes the refusal deterministic. Otherwise it sends only the top 3 sections to Gemini.
>
> Model: `gemini-3.5-flash-lite`. The starter's `gemini-flash-lite-latest` now returns 404 NOT_FOUND, so I changed it in `llm_client.py`.

**What instructions do you give the LLM to keep it grounded?**  
Summarize the rules from your prompt. For example: only use snippets, say "I do not know" when needed, cite files.

> - Answer using only the information in the snippets.
> - Do not invent functions, endpoints or configuration values.
> - If the snippets are not enough, reply exactly "I do not know based on the docs I have."
> - Mention which files were relied on.
> - Each snippet is labelled `File: <name>` so citations are possible.

---

## 4. Experiments and Comparisons

Run the **same set of queries** in all three modes. Fill in the table with short notes.

You can reuse or adapt the queries from `dataset.py`.

| Query | Naive LLM: helpful or harmful? | Retrieval only: helpful or harmful? | RAG: helpful or harmful? | Notes |
|------|---------------------------------|--------------------------------------|---------------------------|-------|
| Where is the auth token generated? | Unhelpful: asked which project I meant | Helpful: top hit is `## Token Generation` | Helpful: `generate_access_token` in `auth_utils.py`, cites AUTH.md | Naive has no docs, so it can't know |
| What environment variables are required for authentication? | **Harmful**: invented `API_KEY`, `CLIENT_ID`, `JWT_SECRET`, `AUTH_DOMAIN` | Helpful but repetitive: 3 overlapping sections from AUTH + SETUP | Helpful: `AUTH_SECRET_KEY` + `TOKEN_LIFETIME_SECONDS`, cites both files | Confident and plausible, but every naive variable name is wrong |
| How do I connect to the database? | **Harmful**: Prisma / Node `pg` code; our project is Python + SQLite/Postgres via `db.py` | Helpful: `## Connection Configuration` first | Helpful: `DATABASE_URL` with SQLite/Postgres examples and the default `app.db` | Naive answer looks impressive and doesn't apply here |
| Which endpoint lists all users? | Unhelpful: asked which API | Helpful but noisy: correct section is in there with a JSON example | Helpful: one line, `GET /api/users` | Clearest case of RAG beating retrieval only |
| What does the /api/projects/<project_id> route return? | **Harmful**: invented `created_at`, `updated_at`, `id` fields | Helpful: correct section ranked first | Helpful: exact JSON from API_REFERENCE.md | Naive mixes real REST conventions with invented fields |
| Is there any mention of payment processing in these docs? | Unhelpful: asked for the docs | Correct refusal | Correct refusal (no LLM call made) | The guardrail works |
| How does a client refresh an access token? | **Harmful**: generic OAuth `/oauth/token` with `grant_type=refresh_token` | Helpful: `## Client Workflow` + `### POST /api/refresh` | Helpful: `POST /api/refresh` with a Bearer header | Naive describes a different protocol |
| Which fields are stored in the users table? | **Harmful**: claims "Based on the database schema…" then invents `id`, `username`, `created_at` | Helpful: `### users` table | Helpful: the 4 real columns | Worst naive example: claims a source it never saw |

**What patterns did you notice?**  

- When does naive LLM look impressive but untrustworthy?  
- When is retrieval only clearly better?  
- When is RAG clearly better than both?

> - **Naive LLM looks impressive but is untrustworthy** whenever the question has a "standard" answer elsewhere. It produced long, well-formatted answers with code examples for databases, OAuth refresh and user-table schemas, all for a project it never saw. It even wrote "Based on the database schema" with no schema in front of it. When it didn't know the context, it sometimes asked for clarification, which is at least honest.
> - **Retrieval only is better than naive** on accuracy every time: everything it shows is real text from the docs. But it is hard to read. You get 3 raw sections (JSON blocks, tables), often with an overlapping or irrelevant one, and you have to find the answer yourself.
> - **RAG is better than both** when the answer is a fact buried in a section. Examples: "Which endpoint lists all users?" → `GET /api/users`, and "Who is allowed to call GET /api/users?" → admins only. It gives a short answer, the evidence and the file names.

---

## 5. Failure Cases and Guardrails

**Describe at least two concrete failure cases you observed.**  
For each one, say:

- What was the question?  
- What did the system do?  
- What should have happened instead?

> **Failure case 1 (RAG wrongly refuses): "What port does the server run on?"**
> - What happened: retrieval returned only `## 3. Initialize the Database`, and Gemini correctly said "I do not know based on the docs I have."
> - Why: the answer (`http://localhost:5000`) is in `## 4. Running the Application`, but the docs never use the word "port", and my stemmer turns "running" into `runn`, which doesn't match the query's "run".
> - What should have happened: retrieve section 4 and answer "port 5000". RAG is only as good as its retrieval; the LLM can't use a section it never receives.

> **Failure case 2 (retrieval ranks the wrong section first): "Who is allowed to call GET /api/users?"**
> - What happened: `### GET /api/projects` ranked above `### GET /api/users`. The projects section says "Returns all projects visible to the calling user", which matches "call" and "user" and wins the tie.
> - Outcome: Gemini still found the right fact ("only accessible to admins", 403 for others) in the second snippet. In retrieval-only mode, though, the first thing you read is the wrong endpoint.
> - What should have happened: the users section should rank first, for example by giving extra weight to an exact path like `/api/users`.

> **Failure case 3 (naive mode): "Which fields are stored in the users table?"**
> - What happened: Naive mode answered "Based on the database schema…" and listed `id`, `username`, `created_at`, `updated_at`.
> - What should have happened: the real columns are `user_id`, `email`, `password_hash`, `joined_at`. A developer who trusted this would write broken queries.

**When should DocuBot say “I do not know based on the docs I have”?**  
Give at least two specific situations.

> 1. The topic is not in the docs at all, e.g. "payment processing", "How do I deploy to Kubernetes?" or "What is the weather today?" All three are refused.
> 2. The question has no meaningful keywords, e.g. "Tell me about it". It's refused because, after removing stopwords, fewer than half of its keywords appear in any section.
> 3. Retrieval found related sections but none contains the specific fact asked for. In that case the LLM, not retrieval, must refuse instead of filling the gap from general knowledge.

**What guardrails did you implement?**  
Examples: refusal rules, thresholds, limits on snippets, safe defaults.

> - **Evidence threshold (`MIN_KEYWORD_COVERAGE = 0.5`)** in `has_enough_evidence()`: a section is used only if it contains at least half of the query's keywords. It uses plain coverage, not the heading-boosted score, so a heading match can't push a weak section past it.
> - **Empty-query rule:** a query with no keywords after removing stopwords never has evidence.
> - **Refusal happens in `retrieve()`**, so retrieval-only mode, RAG mode and `evaluation.py` all share one rule. RAG mode refuses before calling Gemini.
> - **Snippet limit:** at most 3 sections, each a single heading section (about 500 characters or less).
> - **Prompt-level guardrail** (second layer): Gemini must use only the snippets and reply exactly "I do not know based on the docs I have." when they're insufficient. This caught failure case 1.

---

## 6. Limitations and Future Improvements

**Current limitations**  
List at least three limitations of your DocuBot system.

1. **Exact word matching only:** no synonyms or meaning ("port" ≠ `localhost:5000`, "sign in" ≠ "login"), so answerable questions can be refused.
2. **Crude stemmer:** inconsistent forms like "running" → `runn` vs "run" → `run`, and "processes" → `process` vs "process" → `proces`.
3. **Hand-tuned stopwords and threshold:** tuned on 4 small docs and 8 queries; they could over- or under-refuse on a larger doc set.
4. **Score ignores rarity:** a common word like "user" counts as much as a rare, specific one like `refresh`, so ties are frequent (failure case 2).
5. **Naive mode doesn't send the docs**, so it isn't a fair "LLM with full docs" baseline.
6. **Free-tier rate limit:** 15 requests per minute; running all 8 sample queries in modes 1 and 3 back to back hits it.

**Future improvements**  
List two or three changes that would most improve reliability or usefulness.

1. **Weight rare words more (TF-IDF or BM25)** so specific terms like `/api/users` or `refresh` outweigh common words like "user", and give an exact path or code identifier in the query a strong boost.
2. **Use a real stemmer or embeddings** (e.g. a sentence-embedding model) so "port" can match "localhost:5000" and "run" can match "running".
3. **Fix naive mode to include `all_text`** to get a fair "LLM with all docs" comparison against RAG, and add retry-with-backoff for 429 rate-limit errors.

---

## 7. Responsible Use

**Where could this system cause real world harm if used carelessly?**  
Think about wrong answers, missing information, or over trusting the LLM.

> - Naive mode produced confident, well-formatted, invented details: env var names, table columns, and an OAuth flow the project doesn't use. A developer who copies these into code or config could ship bugs, or misconfigure authentication, e.g. by setting `JWT_SECRET` instead of `AUTH_SECRET_KEY` and leaving token signing broken.
> - RAG is grounded, but only in the snippets it receives. If retrieval misses a section (failure case 1), the answer can be incomplete, and a summary can drop important caveats such as "admins only" or "403 for non-admins".
> - Docs can be out of date. DocuBot faithfully repeats whatever the docs say, even when the code has changed.

**What instructions would you give real developers who want to use DocuBot safely?**  
Write 2 to 4 short bullet points.

- Prefer RAG or retrieval-only mode; don't trust naive mode for project-specific facts like names, endpoints or config values.
- Check the cited file before acting, especially for security-related answers (auth, secrets, permissions).
- Treat "I do not know" as a real answer: look in the code or ask a teammate, not a different mode that will guess.
- Keep `docs/` current, because DocuBot can only be as correct as the documentation.
