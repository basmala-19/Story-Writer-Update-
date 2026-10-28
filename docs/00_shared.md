<!-- Shared: Purpose, Architecture, API, Data model, Conventions, Config, Errors, Testing — split from Design Interactive Lesson Stories.md (v2). Shared rules in 00_shared.md -->

# Implementation Specification: Interactive Lesson Stories Service

Version 2 — October 20, 2026

This document is a complete, self-contained specification for building the Interactive Lesson Stories service (story-service) end to end. All decisions in it are final, so an implementing agent should not need to ask anything. External facts that must be looked up (provider API parameters, voice lists, font files) are listed in Section 14, together with fallbacks.

## 1. Purpose, scope and final decisions

**What it builds.** `story-service` is a standalone web service that turns **one topic of one lesson** into a short interactive story: narrated audio, animated vector scenes, on-screen captions and 2 to 4 interactions. The result is one self-contained HTML bundle plus MP3 files. A web page embeds it in a sandboxed iframe, and a mobile app loads it in a locked-down WebView.

**Who calls it.** Another backend (the “caller”). The service knows nothing about the caller’s database. It receives lesson text and metadata and returns a story. Lesson and topic IDs are opaque strings that the service stores and echoes back.

**In scope:** story generation, validation, storage, serving, review and revision, webhooks.

**Out of scope:** parsing books, knowledge graphs, question banks, student data, per-student personalization, analytics, user accounts.

### Final decisions

| # | Decision | Value |
| --- | --- | --- |
| D1 | Unit of work | One request = one topic = one story. A lesson with 3 topics is 3 requests. |
| D2 | Output | One self-contained HTML bundle plus MP3 files. The LLM never writes JavaScript. |
| D3 | Animation | Declarative cues executed by our runtime with the Web Animations API. The LLM does not write CSS keyframes or JS. This keeps seeking, audio sync and validation deterministic. |
| D4 | Visuals | Our own visual system, designed from scratch and independent of any textbook’s look. Scenes are composed deterministically from our asset library. The LLM produces plans and cues, and only rarely a custom prop. |
| D5 | Audience | The story is the same for every student. No personalization and no branching. |
| D6 | Coverage | Every “must” concept is shown in the plot. At least 80% of “important” concepts are covered. |
| D7 | Interaction | 2 to 4 per story (1 to 3 if the target duration is under 150 s). Four types. An interaction never blocks progress. |
| D8 | Validation | After every stage. A failed stage is retried with critic feedback. When attempts run out the version becomes `needs_review`. A version with unresolved blockers or majors is never published. |
| D9 | Human review | Optional, after generation, through comments. Its absence never blocks anything. |
| D10 | Repetition control | Idea memory: the service remembers past story ideas and avoids repeating them. |
| D11 | Languages | `ar` (primary) and `en`. The Arabic dialect is a request field with a fallback rule (stage S0). |
| D12 | Audio | MP3, mono, 24 kHz, CBR 96 kbps. Playback starts on a user tap. |
| D13 | Layout | The stage is 16:9 (1280×720 logical px), scaled to fit and letterboxed. Captions and controls are HTML outside the stage. |
| D14 | Data handling | The service never deletes data on read and has no access to the caller’s database. |
| D15 | Duration | Optional request field `target_duration_sec`. Defaults and ranges per age group (below). Tolerance ±15%. |

### Age groups

| age\_group | Max words per line | Max speakers (incl. narrator) | Default duration | Allowed range | Style |
| --- | --- | --- | --- | --- | --- |
| 6-8 | 14 | 3 | 120 s | 90–150 s | Very short sentences, concrete objects, gentle silly humor, one simple problem. |
| 9-11 | 18 | 4 | 150 s | 120–210 s | Simple cause and effect, mild humor, a small mystery or challenge. |
| 12-14 | 24 | 5 | 180 s | 150–270 s | Relatable social situations, light irony allowed, goals with stakes. |
| 15-18 | 28 | 5 | 210 s | 150–300 s | Realistic scenarios (money, careers, technology), subtle humor, no childish tone. |

Scene count is 5 to 12. A scene lasts 12 to 35 seconds.

## 2. Architecture and tech stack

### Components

- **API service** (FastAPI): accepts requests, reports status, issues play links, serves bundles and packages.
- **Worker** (separate process): claims jobs from the database and runs the pipeline.
- **PostgreSQL 15+** (SQLite allowed for development and tests): all state.
- **Blob store:** bundles, audio and stage artifacts. One interface with `LocalBlobStore` (default, directory `BLOB_DIR`) and `S3BlobStore` (any S3-compatible service).
- **Headless Chromium** (Playwright) for render validation.
- **Provider adapters:** LLM, VLM, TTS and STT, each behind an interface (Section 11).
- **ffmpeg and ffprobe** for audio.

### Stack

| Concern | Choice |
| --- | --- |
| Language | Python 3.11+ |
| API | FastAPI with uvicorn |
| Models and validation | Pydantic v2 |
| Database access | SQLAlchemy 2.x with Alembic migrations |
| HTTP client and retries | httpx, tenacity |
| Render validation | Playwright for Python, Chromium |
| SVG sanitizing | lxml |
| Font subsetting | fonttools (`pyftsubset`) with brotli, output WOFF2 |
| Tests | pytest, pytest-asyncio |
| Lint | ruff |
| Packaging | Docker (base image that includes the Playwright browsers) and docker-compose with services `api`, `worker`, `postgres` |
| Runtime player | Vanilla JavaScript (ES2019), no framework, no build step beyond concatenation, at most 60 KB minified |

### Processes

- API: `uvicorn story_service.api.main:app`.
- Worker: `python -m story_service.worker`. It polls the `jobs` table every 2 s. On Postgres it claims jobs with `FOR UPDATE SKIP LOCKED`. On SQLite only one worker is supported.
- One `run_version` job executes one story version. Inside it the orchestrator runs the stage graph. Scene-level stages run in parallel, bounded by `SCENE_CONCURRENCY`.
- Every stage execution is recorded in `stage_runs`. The orchestrator is resumable: after a restart it skips stage runs that already passed with the same `input_hash`.

### Repository layout

```
story-service/
  pyproject.toml  Dockerfile  docker-compose.yml  .env.example  Makefile
  alembic/
  src/story_service/
    api/         main.py  stories.py  play.py  health.py  schemas.py
    core/        config.py  db.py  security.py  logging.py
    pipeline/    orchestrator.py  stages/s0_intake.py ... s12_publish.py  prompts/*.md  models.py
    providers/   llm/  vlm/  tts/  stt/  blobstore/
    validators/  text.py  svg_sanitizer.py  render.py  vlm_critic.py  interaction.py  bundle.py
    assets/      catalog.json  characters/  backgrounds/  props/  widgets/  fonts/
    runtime/     template.html  player.js  player.css  i18n.json
    worker.py
  tests/         unit/  integration/  e2e/  fixtures/
```

## 3. API contract

Base path `/v1`. JSON is UTF-8. Authentication: header `X-API-Key`, valid keys listed in env `API_KEYS` (comma-separated). Errors always use `{"error": {"code": "...", "message": "...", "details": {}}}`.

### Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| POST | `/v1/stories` | Create a story (one topic). Returns 202. |
| GET | `/v1/stories/{story_id}` | Status, progress and result. |
| GET | `/v1/stories` | List. Filters: `lesson_id`, `topic_id`, `book_id`, `status`, `limit` (default 50, max 200), `cursor`. |
| POST | `/v1/stories/{story_id}/access-link` | Issue a signed play URL. Body `{"ttl_seconds": 21600}` (max 86400). Returns `{"url", "expires_at"}`. |
| GET | `/v1/play/{token}/index.html` | The bundle. No API key; the token authorizes. |
| GET | `/v1/play/{token}/audio/{file}` | An audio file of that bundle. |
| GET | `/v1/stories/{story_id}/package` | ZIP of the published version for offline use (index.html, audio/, meta.json). Needs the API key. |
| POST | `/v1/stories/{story_id}/comments` | Reviewer comment. Triggers a revision (Section 10). |
| POST | `/v1/stories/{story_id}/versions/{n}/approve` | Publish a `needs_review` version. |
| POST | `/v1/stories/{story_id}/rollback` | Body `{"to_version": n}`. |
| POST | `/v1/stories/{story_id}/regenerate` | Full regeneration as a new version. |
| GET | `/v1/health` | Liveness and readiness (database, blob store). |

### Create request

```json
{
  "external": {"lesson_id": "L-1042", "topic_id": "T-3", "book_id": "B-77"},
  "lesson_text": "full lesson text",
  "topic": {"title": "Comparing fractions", "text": null},
  "metadata": {
    "country": "EG", "language": "ar", "dialect": null,
    "age_group": "9-11", "grade": "4", "subject": "math",
    "target_duration_sec": null, "voice_overrides": null
  },
  "callback_url": null,
  "force": false
}
```

| Field | Rules |
| --- | --- |
| `external.lesson_id`, `external.topic_id` | Required strings, 1–128 chars. `book_id` optional. Stored and echoed back, never interpreted. |
| `lesson_text` | Required, 200–60,000 chars. The whole lesson, used as context. |
| `topic.title` | Required, 1–200 chars. |
| `topic.text` | Optional, 200–20,000 chars. When null, the worker locates the topic inside `lesson_text` (stage S0). |
| `metadata.country` | Required, ISO 3166-1 alpha-2, uppercase. |
| `metadata.language` | `ar` or `en`. Default `ar`. |
| `metadata.dialect` | Null or one of `msa`, `egyptian`, `gulf`, `levantine`, `maghrebi` (only when language is `ar`). Null means derive from country (stage S0). |
| `metadata.age_group` | Required: `6-8`, `9-11`, `12-14` or `15-18`. |
| `metadata.grade`, `metadata.subject` | Required free-text strings (at most 16 and 64 chars), used in prompts. |
| `metadata.target_duration_sec` | Null or integer 60–420. Clamped to the age group’s range. |
| `metadata.voice_overrides` | Null or `{"narrator": "<voice_id>"}`. Otherwise voices are assigned automatically from the cast (Section 9). |
| `callback_url` | Null or an https URL. |
| `force` | If true, create a new story even when an identical one exists. |

**Idempotency.** The natural key is the SHA-256 of `lesson_id`, `topic_id`, `topic.title`, a hash of `lesson_text`, and the metadata fields country, language, dialect, age\_group, grade, subject. The `Idempotency-Key` header, when present, replaces it. If a non-failed story with the same key exists and `force` is false, return it with HTTP 200 instead of creating a new one.

**Response 202:** `{"story_id": "<uuid>", "version": 1, "status": "queued"}`.

### Get response

```json
{
  "story_id": "…",
  "external": {"lesson_id": "L-1042", "topic_id": "T-3", "book_id": "B-77"},
  "status": "queued | running | published | needs_review | failed",
  "current_version": 2,
  "published_version": 1,
  "progress": {"stage": "S6_audio", "completed_stages": 6, "total_stages": 13, "percent": 46},
  "result": {
    "title": "…", "language": "ar", "dialect_applied": "msa", "age_group": "9-11",
    "duration_ms": 181400, "scene_count": 8, "interaction_count": 3,
    "coverage": {
      "must": [{"concept_id": "c1", "title": "…", "scene_ids": ["s02", "s03"]}],
      "important_covered_ratio": 1.0,
      "omitted": []
    },
    "warnings": [{"code": "DIALECT_FALLBACK", "message": "…"}],
    "sizes": {"bundle_bytes": 812345, "audio_bytes": 2900000, "audio_files": 24}
  },
  "issues": [],
  "created_at": "…",
  "updated_at": "…"
}
```

`status` is the status of the latest version. `result` describes the **published** version if one exists, otherwise the latest. `issues` lists unresolved blockers and majors (`{code, severity, stage, scene_id, message}`) when the status is `needs_review` or `failed`. A story is playable only when `published_version` is not null.

### Webhook

When `callback_url` is set, POST `{"event": "story.published | story.needs_review | story.failed", "story_id", "version", "external", "status"}` with headers `X-Signature` (hex HMAC-SHA256 of the raw body using `WEBHOOK_SECRET`) and `X-Event`. Retry up to 6 times with exponential backoff starting at 30 s. Polling the GET endpoint remains the source of truth.

### Play URL and security headers

The token is `base64url(story_id|version|expires_epoch|hmac)` where `hmac` is HMAC-SHA256 over the first three fields with `PLAY_TOKEN_SECRET`. The token sits in the path so relative audio paths (`audio/s01_l01.mp3`) resolve under it. `/v1/play/...` responses carry:

- `Content-Security-Policy: default-src 'none'; script-src 'unsafe-inline'; style-src 'unsafe-inline'; font-src data:; img-src data:; media-src 'self'; connect-src 'none'; base-uri 'none'; form-action 'none'; frame-ancestors <ALLOWED_EMBED_ORIGINS>; sandbox allow-scripts`
- `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `Cache-Control: private, max-age=3600`.

Callers embed it as `<iframe src="{url}" sandbox="allow-scripts">`. The page runs with an opaque origin and cannot read the caller’s cookies or storage or call any API. An integration test must load the bundle under exactly these headers in Chromium and confirm audio plays. If `media-src 'self'` blocks audio under the sandbox, replace `'self'` with the explicit value of `PUBLIC_BASE_URL`.

## 4. Data model

All primary keys are UUIDs unless stated. All timestamps are UTC. Create the schema with Alembic.

| Table | Columns |
| --- | --- |
| `stories` | id; external\_lesson\_id; external\_topic\_id; external\_book\_id (null); natural\_key (unique); country (char 2); language; dialect\_requested (null); dialect\_applied (null); age\_group; grade; subject; target\_duration\_sec; callback\_url (null); published\_version (int, null); created\_at; updated\_at |
| `story_versions` | id; story\_id (fk); version (int); status (`queued`, `running`, `published`, `needs_review`, `failed`, `superseded`); trigger (`create`, `regenerate`, `revision`); parent\_version (int, null); request\_json; issues\_json; started\_at; finished\_at. Unique (story\_id, version). |
| `stage_runs` | id; version\_id (fk); stage; scene\_id (null); attempt (int); status (`running`, `passed`, `failed`, `skipped`); input\_hash; artifact\_id (fk, null); validation\_json; usage\_json (`model`, `tokens_in`, `tokens_out`, `latency_ms`); error\_json; started\_at; finished\_at |
| `artifacts` | id; version\_id; stage; scene\_id (null); kind (`json`, `svg`, `mp3`, `html`, `png`, `zip`); blob\_key; sha256; bytes; created\_at |
| `jobs` | id; kind (`run_version`, `revision`, `deliver_webhook`); payload\_json; status (`queued`, `running`, `done`, `dead`); attempts; run\_after; locked\_by (null); locked\_at (null); last\_error (null) |
| `localization_briefs` | id; country; language; dialect; age\_group; brief\_json; created\_at; expires\_at. Unique (country, language, dialect, age\_group). |
| `idea_memory` | id; story\_id; country; language; age\_group; subject; one\_liner; idea\_json; created\_at. Index (country, language, age\_group, subject, created\_at). |
| `review_comments` | id; story\_id; version; scene\_id (null); t\_ms (null); body; author (null); status (`open`, `batched`, `applied`, `rejected`); resulting\_version (null); created\_at |
| `webhook_deliveries` | id; story\_id; version; event; payload\_json; attempts; last\_http\_status (null); next\_attempt\_at; delivered\_at (null) |

**Rules.**

- Blob keys: `stories/{story_id}/v{version}/{stage}/{scene_id or _}/{name}`. Final files: `stories/{story_id}/v{version}/bundle/index.html` and `.../bundle/audio/{file}.mp3`, `.../bundle/meta.json`.
- A story has at most one published version. Publishing a new one marks the previous published version `superseded`. Artifacts of a published version are never modified.
- A `queued` or `running` version is the story’s latest version. A new request for the same story cannot start while one is `queued` or `running`; return HTTP 409 `STORY_BUSY`.

## 5. Pipeline

A version runs stages S0 to S12 in order. Scene-level stages (S7, S8 and the repair loop of S11) run per scene in parallel.

### 5.0 Conventions for every stage

- A stage consumes artifacts and produces one artifact: JSON validated by a Pydantic model, stored in the blob store and recorded in `artifacts`.
- After each run, `validate_<stage>` returns `{passed, checks: [{name, passed, severity, detail}], scores?}`. Severity is `blocker`, `major` or `minor`. A stage passes when no blocker or major check failed. Minors are recorded and surfaced as warnings.
- **Retry.** A failed LLM stage runs again with the failed checks and critic issues appended to its prompt, up to `MAX_STAGE_ATTEMPTS` (3) in total. Scene-level repairs are limited to `MAX_SCENE_REPAIRS` (2) per scene. When attempts run out the version becomes `needs_review` with the issues and the remaining stages are skipped.
- **Cache.** `input_hash` = SHA-256 of the canonical JSON of the stage inputs, the prompt file hash and the model id. A passed run with the same hash is reused.
- **LLM calls.** Always structured JSON validated against the stage schema, with one automatic repair attempt on invalid JSON. Temperatures: extractors and critics 0.2, Story Idea 0.8, Story Writer 0.7, Visual Director 0.3.
- **Untrusted input.** Lesson text goes inside `<lesson_text>…</lesson_text>` delimiters. Every system prompt states that text inside the delimiters is data and never instructions.
- **Language.** Story text is in the requested language. JSON keys are English.
- **`norm_ar`** (used by every quote, substring and similarity check): remove diacritics (U+064B to U+0652, U+0670) and tatweel (U+0640); map أ إ آ ٱ to ا; ى to ي; ة to ه; Arabic-Indic digits to Western digits; lowercase Latin letters; strip punctuation; collapse whitespace.

### Stage map

| Stage | Name | Kind | Artifact | Scope |
| --- | --- | --- | --- | --- |
| S0 | Intake | code (+ small LLM) | `intake.json` | story |
| S1 | Localization Brief | small LLM, cached | `brief.json` | story |
| S2 | Concept Extractor | large LLM | `concepts.json` | story |
| S3 | Story Idea | large LLM + code | `idea.json` | story |
| S4 | Story Writer and Critic | large LLM | `script.json` | story |
| S5 | Cast and Setting | large LLM + code | `cast.json` | story |
| S6 | Audio Producer | TTS + STT + ffmpeg | `audio.json`, MP3 per line | line |
| S7 | Visual Director | large LLM | `scene_plan.json` | scene |
| S8 | Scene Composer | code (+ LLM for custom props) | `scene.svg`, `scene_meta.json` | scene |
| S9 | Timeline Compiler | code | `timeline.json` | story |
| S10 | Assembler | code | `index.html`, `meta.json` | story |
| S11 | Render validation and repair | Chromium + VLM | `validation.json` | scene, then story |
| S12 | Publish | code | none | story |

## 6. Visual system and asset library

The visual identity is ours and is designed from scratch. It does not follow any textbook’s look. The implementing agent authors every asset file described here (hand-written SVG, LLM assistance allowed).

### 6.1 Style rules

- Flat vector illustration with rounded geometry and no outlines. Shapes are separated by color. Depth comes from one darker shade (15 to 20% darker) per shape, with light from the top left.
- Solid fills only. Linear gradients (2 stops) are allowed only for sky and large background areas.
- No filters, no blur, no masks (only `clipPath`), no raster images.
- Faces are simple: two eyes, brows, a mouth. No exaggerated proportions or features. All variation comes from the parameters below.
- Canvas 1280×720. The ground line is at y = 640 unless a background declares otherwise (`ground_y` in the catalog).
- Asset files use CSS variables from the token table for every color. No hard-coded hex colors and no `rgb()` values inside asset files.

### 6.2 Tokens

Ages 6-8 and 9-11 use the **bright** column. Ages 12-14 and 15-18 use the **calm** column.

| Token | bright | calm |
| --- | --- | --- |
| `--ink` | #1E2A3A | #1E2A3A |
| `--paper` | #FFF9F0 | #F7F5F0 |
| `--c-primary` | #FF7A59 | #E4572E |
| `--c-secondary` | #2EC4B6 | #17BEBB |
| `--c-accent` | #FFC93C | #FFC914 |
| `--c-support` | #6C63FF | #4B5D8C |
| `--c-neutral-1` | #D9D4CC | #D9D4CC |
| `--c-neutral-2` | #8E99A8 | #8E99A8 |
| `--sky-day` / `--ground-day` | #BEE9FF / #A8E6A1 | #CFE8F7 / #B7D9B0 |
| `--sky-evening` / `--ground-evening` | #FFD3A5 / #8FD19E | #F6C89F / #9CC49A |
| `--sky-night` / `--ground-night` | #1F2A55 / #2F4F4F | #1B2340 / #2A3F3F |

The scene group sets `--sky` and `--ground` from the scene’s `time_of_day`. Backgrounds use only `--sky`, `--ground` and the tokens above.

Skin tones `--skin-0` to `--skin-5`: #FDDCC0, #F5C6A0, #E0A878, #C68642, #8D5524, #5C3A21. Hair colors `--hair-0` to `--hair-5`: #1B1B1B, #3B2A20, #6B4423, #A0522D, #D2A24C, #8A8A8A.

**Typography.** Cairo (covers Arabic and Latin), weights 400 and 700. Get the font files from the `google/fonts` repository (SIL Open Font License), vendor them into `assets/fonts/` with the license text. Captions and interaction cards use 700.

## 11. Configuration, providers and limits

### 11.1 Environment variables

Provide `.env.example` with every variable below. “Required” variables have no default.

| Variable | Default | Meaning |
| --- | --- | --- |
| `DATABASE_URL` | required | SQLAlchemy URL. Postgres in production, SQLite allowed in dev and tests. |
| `BLOB_BACKEND` | `local` | `local` or `s3`. |
| `BLOB_DIR` | `./data/blobs` | Directory for the local backend. |
| `S3_ENDPOINT_URL`, `S3_BUCKET`, `S3_ACCESS_KEY_ID`, `S3_SECRET_ACCESS_KEY`, `S3_REGION` | none | Used when `BLOB_BACKEND=s3`. |
| `API_KEYS` | required | Comma-separated valid API keys. |
| `PLAY_TOKEN_SECRET` | required | At least 32 random bytes. Signs play tokens. |
| `WEBHOOK_SECRET` | required if callbacks are used | Signs webhook bodies. |
| `PUBLIC_BASE_URL` | required | Base URL used to build play links. |
| `ALLOWED_EMBED_ORIGINS` | required | Space-separated origins allowed in `frame-ancestors`. |
| `PLAY_LINK_TTL_SEC` | 21600 | Default play link lifetime. |
| `RATE_LIMIT_RPM` | 120 | Requests per minute per API key. |
| `LLM_PROVIDER` | `anthropic` | `anthropic` or `openai_compatible`. |
| `ANTHROPIC_API_KEY` | none | For provider `anthropic`. |
| `OPENAI_BASE_URL`, `OPENAI_API_KEY` | none | For provider `openai_compatible`. |
| `LLM_MODEL_LARGE` | `claude-sonnet-5-5` | Writer, critics, director, revision planner. |
| `LLM_MODEL_SMALL` | `claude-haiku-5-5` | Topic locator, localization brief, reviewers. |
| `LLM_MODEL_VISION` | `claude-sonnet-5-5` | VLM critic and custom prop check. |
| `LLM_MAX_CONCURRENCY` | 8 | Concurrent LLM calls per process. |
| `TTS_PROVIDER`, `STT_PROVIDER` | `groq` | Provider registry keys. |
| `TTS_MODEL` | `canopylabs/orpheus-arabic-saudi` | Arabic TTS model. |
| `TTS_MODEL_EN` | none | English TTS model (Section 14). |
| `STT_MODEL` | `whisper-large-v3` | STT model. |
| `GROQ_API_KEY` | none | For provider `groq`. |
| `WORKER_CONCURRENCY` | 2 | Versions processed in parallel per worker. |
| `SCENE_CONCURRENCY` | 4 | Scenes processed in parallel per version. |
| `TTS_CONCURRENCY` | 4 | Lines synthesized in parallel. |
| `MAX_STAGE_ATTEMPTS` | 3 | Attempts per stage. |
| `MAX_SCENE_REPAIRS` | 2 | Repairs per scene in S11. |
| `MAX_TOKENS_PER_STORY` | 600000 | Sum of input and output tokens across stages. Exceeding it fails the version with `COST_LIMIT_EXCEEDED`. |
| `BRIEF_TTL_DAYS` | 90 | Localization brief cache lifetime. |
| `NARRATOR_GENDER` | `f` | `f` or `m`. |
| `DISPLAY_DIGITS` | `western` | `western` or `arabic_indic`, for captions and widget labels. |
| `SPEECH_WPS_AR`, `SPEECH_WPS_EN` | 2.4, 2.7 | Words per second used for duration estimates. |
| `REVISION_BATCH_WINDOW_SEC` | 30 | Comment batching window. |
| `REVISION_REQUIRES_APPROVAL` | `false` | If true, revisions end as `needs_review`. |
| `LOG_LEVEL` | `INFO` | Log level. |

### 11.2 Provider interfaces

- **`LLMClient`:** `complete_json(system, user, schema, model_role, temperature, max_tokens, images=None)` returns a parsed dict. `model_role` is `large`, `small` or `vision`. The implementation uses the provider’s structured-output mechanism (Section 14), retries transient errors (Section 12) and records usage (`tokens_in`, `tokens_out`, `latency_ms`).
- **`TTSProvider`, `STTProvider`:** Section 9.1.
- **`BlobStore`:** `put(key, bytes)`, `get(key)`, `exists(key)`, `url_for(key)` (not used by the API), `list(prefix)`.
- **Test doubles** `FakeLLM`, `FakeTTS`, `FakeSTT`, `LocalBlobStore` (Section 13).

### 11.3 Timeouts and limits

LLM call 120 s. VLM call 120 s. TTS call 60 s. STT call 90 s. Render validation per scene 60 s. A whole version 45 minutes; on expiry it fails with `TIMEOUT`. Request body limit 1 MB.

## 12. Failure policy, errors and retries

### 12.1 Error codes

| Code | Where | Meaning |
| --- | --- | --- |
| `INPUT_INVALID` | HTTP 422 | Request failed schema validation. |
| `UNAUTHORIZED` | HTTP 401 | Missing or invalid API key or play token. |
| `RATE_LIMITED` | HTTP 429 | Too many requests for this key. |
| `NOT_FOUND` | HTTP 404 | Unknown story, version or file. |
| `STORY_BUSY` | HTTP 409 | A version of this story is queued or running. |
| `INPUT_TOO_SHORT` | version `failed` | The topic text is under 200 characters. |
| `TOPIC_NOT_FOUND` | version `failed` | The topic could not be located in the lesson text. |
| `LANGUAGE_MISMATCH` | version `failed` | The text is not in the requested language. |
| `PROVIDER_ERROR` | version `failed` | A non-retryable provider failure (authentication, no matching voice, unsupported language). |
| `COST_LIMIT_EXCEEDED` | version `failed` | `MAX_TOKENS_PER_STORY` was exceeded. |
| `TIMEOUT` | version `failed` | The version exceeded 45 minutes. |
| `INTERNAL` | version `failed` | A code defect or a job that died 5 times. |
| `VALIDATION_EXHAUSTED` | version `needs_review` | A stage ran out of attempts. `issues` lists what is unresolved. |

### 12.2 Retry rules

- **Transient provider errors** (HTTP 429 and 5xx, timeouts, connection resets): retry with exponential backoff of 2, 4, 8, 16 and 32 seconds with ±20% jitter, at most 5 tries. They do not count as stage attempts.
- **Invalid JSON from an LLM:** one automatic repair attempt within the same stage attempt.
- **Validation failure:** counts as a stage attempt (Section 5.0).

### 12.3 Crash recovery and resumability

A `running` job whose `locked_at` is older than 10 minutes is re-queued with `attempts + 1`. After 5 attempts it becomes `dead` and its version `failed` with `INTERNAL`. Stage runs with status `passed` and an unchanged `input_hash` are reused when a job restarts.

### 12.4 Other rules

- Failed and `needs_review` versions keep their artifacts for debugging. They are never served by the play endpoints.
- Webhook failures never change a story’s status.
- Logs are structured JSON with `story_id`, `version`, `stage`, `scene_id` and `attempt`. Never log lesson text. Log its hash and length instead.
- Lesson text is sent only to the configured LLM and VLM providers.

## 13. Testing and acceptance criteria

### 13.1 Test doubles

- `FakeLLM` returns canned, schema-valid JSON per stage from `tests/fixtures/llm/`.
- `FakeTTS` returns a WAV sine tone whose length is 0.4 s per word.
- `FakeSTT` returns the words of the given text spaced evenly across the audio duration.
- A hand-authored fixture set for the sample request (Section 15) covers every stage output, including a valid script, cast and scene plans.

### 13.2 Unit tests

`norm_ar`; topic locating; dialect mapping; duration estimate; Jaccard memory check; every Pydantic stage model; the SVG sanitizer (it accepts every library asset and rejects each rejected construct, one test per rule); timeline compiler (cue resolution, gaps, interaction offsets); ASR-to-display token alignment including digits; play token signing and expiry; idempotency; version state machine (publish, superseded, rollback); job claiming by two workers (exactly one wins); resume after a simulated crash.

### 13.3 Integration tests

- Create, poll, issue an access link, load the bundle in Chromium under the exact production headers, and confirm audio plays.
- Webhook signature and retry behavior.
- Comment, revision, approve and rollback (with fakes).

### 13.4 Live smoke test

Marked `live` and run manually with real keys: the sample request must end `published` or `needs_review` with every stage artifact present. Record tokens per stage.

### 13.5 Acceptance criteria

The service is done when all of these hold:

1. All unit, integration and golden end-to-end tests pass in CI (`make test`).
2. The golden bundle passes S10 and S11 with zero blockers and zero majors on V1, V2 and V3.
3. The asset library acceptance (6.8) passes.
4. With real providers, the sample request produces a published story with: duration within ±15% of the 150 s target; 5 to 12 scenes; 2 to 4 interactions; every `must` concept shown in the plot according to the Story Critic; `index.html` at most 1.5 MB.
5. Under the production headers, the bundle plays in Chromium: audio progresses, interactions open and close, and the end screen appears.
6. Sending the same request twice returns the same story. With `force=true` it creates a new story whose idea differs from the first (Jaccard similarity of `one_liner` below 0.5).
7. Killing the worker mid-run and restarting resumes without repeating passed stages (check `stage_runs`).
8. The security regression test (8.7) passes.
9. `docker compose up` starts `api`, `worker` and `postgres` from a clean checkout with only `.env` filled in, and `/v1/health` reports ready.

## 14. Implementation order and external facts to look up

### 14.1 Order

| Milestone | Deliverable |
| --- | --- |
| M0 | Repository scaffold, config, database models and migrations, API skeleton with auth and health, blob store, job claim loop. |
| M1 | LLM, VLM, TTS and STT adapters, test doubles, `voices.yaml`. |
| M2 | The deterministic core: asset library (Section 6), catalog, composer, runtime (Section 7), assembler, sanitizer. Prove it with a hand-written script and scene plans that produce a bundle which opens and plays with fake audio. |
| M3 | Render validators and interaction tests (8.3 to 8.6) running on the M2 bundle. |
| M4 | Text stages S0 to S5 with their validators and fixtures. |
| M5 | Audio stage S6, word timings and the Timeline Compiler S9. |
| M6 | S7 and S8 integrated. The full pipeline with fakes, the orchestrator with retries, resume and the repair loop. |
| M7 | Play endpoints, access links, package ZIP, webhooks. |
| M8 | Comments, revision, approve, rollback, regenerate. |
| M9 | Hardening: rate limits, timeouts, token budget breaker, logging, a load test of 10 concurrent stories, README and runbook. |

### 14.2 External facts the implementer must look up

These are provider or tool facts, not product decisions. Look them up in the official documentation when needed, and use the fallback if the lookup fails.

| Item | Source | Fallback |
| --- | --- | --- |
| Structured-output mechanism of the chosen LLM provider | Provider docs | Prompt for JSON, validate against the schema, one repair attempt. |
| Groq TTS: model ids, available voices, maximum characters per request, rate limits, response format | Groq docs | If voices cannot be listed, use the provider’s documented default voices and pitch shifting (9.2). |
| Groq STT: the parameter for word-level timestamps | Groq docs | Use `timing_source = estimated`. |
| English TTS model for `TTS_MODEL_EN` | Provider docs | Reject `en` requests with `INPUT_INVALID` until it is configured. |
| Cairo font files | `google/fonts` GitHub repository, directory `ofl/cairo` | Fetch with `scripts/fetch_fonts.py` at build time. As a last resort use another OFL font covering Arabic and Latin, and record the change. |
| Playwright and Chromium CPU throttling | Playwright and CDP docs | Skip the throttle and downgrade the performance check to minor. |
| ffmpeg filter syntax (`loudnorm`, `silenceremove`, pitch shifting) | ffmpeg docs | Skip pitch shifting and reuse the voice unchanged. |
| Whether `media-src 'self'` allows audio under `sandbox` | The integration test in 13.3 | Use the explicit `PUBLIC_BASE_URL` value (Section 3). |

### 14.3 Rules for the implementing agent

- Do not change decisions D1 to D15, and do not invent endpoints or fields that are not in this document.
- The LLM never writes JavaScript for the bundle.
- Never skip or loosen a validator to make a test pass. Fix the cause.
- If something is ambiguous, choose the simplest option consistent with D1 to D15 and record it in `docs/DECISIONS.md`.

## 15. Appendix: sample request

Use this as the golden fixture and the live smoke test. A second request for the same lesson uses `topic.title` “مقارنة الكسور” and `topic_id` `T-2`.

```json
{
  "external": {"lesson_id": "L-1", "topic_id": "T-1", "book_id": "B-1"},
  "lesson_text": "الكسور\n\nمفهوم الكسر\nالكسر جزء من كل. عندما نقسم شيئًا إلى أجزاء متساوية، يسمى كل جزء كسرًا من هذا الشيء. نكتب الكسر على صورة عددين بينهما خط: العدد الأسفل هو المقام، ويدل على عدد الأجزاء المتساوية التي قُسم إليها الكل. والعدد الأعلى هو البسط، ويدل على عدد الأجزاء التي نأخذها. مثال: إذا قسمنا قطعة بيتزا إلى 4 أجزاء متساوية وأكلنا جزءًا واحدًا، فقد أكلنا ربع البيتزا، ونكتب ذلك 1/4.\n\nمقارنة الكسور\nعندما يكون المقام واحدًا في كسرين، يكون الكسر الأكبر هو الذي بسطه أكبر، فالكسر 3/8 أكبر من 2/8. وعندما يكون البسط واحدًا، يكون الكسر الأكبر هو الذي مقامه أصغر، فالكسر 1/2 أكبر من 1/4، لأن الأجزاء الأقل عددًا تكون أكبر حجمًا.",
  "topic": {"title": "مفهوم الكسر", "text": null},
  "metadata": {
    "country": "EG", "language": "ar", "dialect": null,
    "age_group": "9-11", "grade": "4", "subject": "math",
    "target_duration_sec": null, "voice_overrides": null
  },
  "callback_url": null,
  "force": false
}
```

**Expected behavior with the default providers:**

- S0 locates the topic span (about 380 characters), derives `egyptian` from the country, and because the default TTS supports only `gulf` and `msa`, sets `dialect_applied` to `msa` and adds the warning `DIALECT_FALLBACK`. The target duration is 150 s.
- S2 extracts two `must` concepts: a fraction is one part of an equal-parts whole, and numerator versus denominator. The comparison rules belong to the second topic and must not be taught in this story (they are in `later_preview`).
- S3 to S4 produce a story around an everyday situation for a child in Egypt, with at least one interaction on the numerator and denominator.
