# Mentora

### Senior–Junior Career Mentorship and Institutional Knowledge Platform

Mentora is a college-focused platform that helps juniors learn from the experiences of seniors and alumni.

It preserves useful stories about learning, projects, interviews and mistakes, then makes reviewed experiences searchable. Students can ask questions, inspect the evidence supporting an AI answer and follow practical learning roadmaps.

> **Project status:** Planning stage. Development has not started. The features and technology choices below describe the planned implementation.

## The Problem

Useful guidance often stays in personal chats or disappears when seniors graduate.

Juniors may struggle to:

- Find seniors with relevant experience.
- Get guidance suited to their college and academic context.
- Learn from previous students’ mistakes.
- Check the sources behind AI-generated advice.
- Decide what to learn next.

Mentora aims to preserve this knowledge and make it useful for future batches.

## Target Users

- **Juniors:** Discover experiences, ask questions and follow roadmaps.
- **Seniors and alumni:** Contribute experiences and, in a later release, offer mentoring.
- **Institution moderators:** Review submissions and handle reports.
- **Platform administrators:** Manage restricted access and operational settings.

The initial pilot will focus on **one institution, one department and one software-development career track**.

## Planned Features

### Core MVP

- Account registration, login and institution membership.
- Student profiles and contributor verification.
- Structured contributions covering journeys, projects, interviews and mistakes.
- Private drafts and submission for review.
- Moderator approval and requests for changes.
- Keyword search with topic and academic-context filters.
- Source details with dates, context and limitations.
- AI answers supported by citations to reviewed experiences.
- Clear insufficient-evidence responses.
- Source versioning, correction and withdrawal.
- Reports for unsupported answers, outdated guidance and abuse.

### Pilot Increment

- One reviewed learning roadmap.
- Prerequisites and completion criteria for roadmap steps.
- Evidence links supporting relevant recommendations.
- Private progress tracking.
- Mobile and accessibility improvements.
- Quality, usability, reliability and cost evaluation.

### Later Releases

- Mentor discovery and availability.
- Mentorship requests and status tracking.
- Anonymous peer questions with restricted moderator access to identity.
- Question threads and human responses.
- Different perspectives on the same topic.
- Knowledge-gap reporting to identify missing contributions.

### Outside the Initial Scope

- Live chat and video calls.
- Payments.
- Placement predictions or guarantees.
- Automatic scraping.
- Custom language-model training.
- A broad multi-institution launch.

## How Mentora Will Work

### Sharing an Experience

1. A verified contributor saves a structured draft.
2. The contributor submits it for review.
3. An institution moderator approves it or requests changes.
4. Approved, consented content becomes discoverable.
5. The contributor can later submit a correction or withdraw the source.

### Asking a Question

1. A student submits a question.
2. The backend checks membership and usage allowance.
3. A background worker retrieves relevant, permitted passages.
4. The AI generates an answer using those passages.
5. The student sees the answer and its supporting evidence.
6. If the evidence is insufficient, Mentora says so.

### Following a Roadmap

1. A student selects a supported learning goal.
2. Mentora uses reviewed prerequisites and relevant evidence.
3. The student follows the ordered steps and updates progress.
4. Roadmap revisions preserve previously completed work.

## Planned Technology Stack

| Area | Technology |
|---|---|
| Frontend | React, TypeScript and Vite |
| Routing and styling | React Router and Tailwind CSS |
| Data fetching | TanStack Query |
| Forms and validation | React Hook Form and Zod |
| Backend | Python and FastAPI |
| API schemas | Pydantic |
| Database access | SQLAlchemy 2 |
| Database migrations | Alembic |
| Database | PostgreSQL |
| Search | PostgreSQL full-text search and pgvector |
| Authentication | Supabase Auth |
| AI integration | Official model SDK and an explicit RAG pipeline |
| Background processing | Durable SQL jobs and a Python worker |
| Backend tests | pytest and HTTPX |
| Frontend tests | Vitest and React Testing Library |
| Browser tests | Playwright |
| Load tests | Locust |
| Build and delivery | Docker and GitHub Actions |

**Planned model baselines:** `text-embedding-3-small` for embeddings and `gpt-5-mini` for answer generation.

**Planned hosting baseline:** Cloudflare Pages for the frontend, Render for API and worker processes, and Supabase for managed supporting services.

Model availability, provider limits and costs will be checked before configuration. Hosting plans will be selected within the team’s agreed budget.

## Architecture

Mentora will use a **modular backend with a separate background worker**.

- The React application presents student and moderator screens.
- FastAPI validates requests and checks current permissions.
- PostgreSQL stores memberships, source versions, citations, roadmaps and jobs.
- The worker processes indexing and AI-generation jobs.
- External model services receive only the permitted information needed for the task.

The API and worker will share backend code but run as separate processes.

Authentication identifies the user. Backend authorization decides what that user can access or change.

## AI and RAG Approach

Mentora will use **Retrieval-Augmented Generation (RAG)**.

Before generating an answer, the system retrieves relevant passages from approved experiences. The answer must refer to the evidence used.

The initial approach will include:

- Section-aware chunking.
- Embedding generation.
- Keyword and vector retrieval.
- Hybrid ranking and deduplication.
- Citation and claim-support checks.
- Insufficient-evidence handling.
- Source eligibility checks before saving and reading results.

We do not plan to train a new language model.

The evaluation will compare keyword, vector and hybrid retrieval using the same held-out questions and source corpus.

## Team and Responsibilities

| Member | Team | Main Responsibilities |
|---|---|---|
| **Ankit Singh** | Backend | Architecture, database schema, authentication, permissions, contribution lifecycle, core APIs and integration |
| **Naman** | Backend | Moderation and roadmap APIs, background worker, integration testing, CI, deployment and recovery |
| **Dhruv** | Frontend | Student portal, shared UI components, contribution forms, search and AI-answer screens |
| **Parth** | Frontend | Moderator panel, reports and feedback screens, roadmap and progress UI |
| **Shreya** | AI/ML | Chunking, embeddings, retrieval, ranking and cited-answer pipeline |
| **Anshuman** | AI/ML | Evaluation dataset, quality checks, error analysis, prerequisites and roadmap recommendation logic |

### Ownership Boundaries

- Dhruv builds shared components; Parth reuses them.
- Parth builds moderation and roadmap screens; Naman builds their backend APIs.
- Ankit owns shared authentication, permission and publication services.
- Shreya builds AI handlers; Naman executes them through the worker.
- Anshuman builds roadmap logic; Naman persists results and Parth displays them.
- Every member tests their own features. Naman coordinates overall integration testing.

## Proposed Repository Structure

The following structure will be created during project setup:

| Directory | Purpose |
|---|---|
| `frontend/` | React application and frontend tests |
| `backend/` | FastAPI modules, worker, migrations and backend tests |
| `evaluation/` | Reviewed datasets, evaluation runners and results |
| `tests/e2e/` | Integrated browser tests |
| `deployment/` | Deployment configuration |
| `docs/` | PRD, architecture, contracts and operating guides |

AI modules will live inside the shared backend where needed. Evaluation experiments will remain in `evaluation/`.

## Development Roadmap

Week numbers start from the agreed project kickoff.

| Phase | Period | Main Deliverable |
|---|---|---|
| Planning and setup | Week 1 | Scope, contracts, schema, wireframes and local setup |
| Core MVP | Weeks 2–3 | Accounts, contributions, moderation and keyword discovery |
| Evidence and AI | Weeks 4–5 | Worker, embeddings, hybrid retrieval, citations and withdrawal |
| Roadmaps and hardening | Weeks 6–7 | One roadmap, private progress, feedback and release checks |
| Supervised pilot | Weeks 8–11 | Usability, quality, reliability and cost observations |
| Evaluation and handover | Week 12 | Final report, demonstration and maintenance documentation |

The first integration milestone is:

**Senior submits → moderator approves → junior discovers the experience.**

## Privacy and Reliability Requirements

- Drafts remain private to their owners and permitted reviewers.
- Institution access is checked on the backend.
- Published source versions are preserved for provenance.
- Withdrawal immediately removes a source from new retrieval.
- Existing answers recheck access to their supporting sources.
- Roadmap progress and answer history remain owner-private.
- API keys, tokens and verification documents stay out of public code and logs.
- Anonymous identity mappings stay out of peer responses and AI context.
- AI source text is treated as untrusted data.
- Background jobs survive restarts and use bounded retries.
- Provider failures and exhausted allowances produce clear user states.

Reviewed experience is contextual guidance. It does not guarantee factual correctness or placement outcomes.

## Testing and Evaluation

The planned checks include:

- Unit tests for state transitions, validation and recommendation rules.
- Integration tests for permissions, publication, retries and withdrawal.
- Browser tests for the complete student and moderator journeys.
- Security tests for cross-institution access and privilege escalation.
- Worker restart and provider-outage tests.
- Accessibility and mobile checks.
- Load tests and backup-restoration exercises.

AI evaluation will measure:

- Retrieval Recall@k.
- Mean Reciprocal Rank.
- Citation and claim support.
- Answer coverage.
- Correct abstention.
- Latency and token cost.

All reported results will include their dataset, configuration and sample size. Targets will not be presented as achieved results.

## Documentation

The project documentation has been prepared and will be added to the repository:

- Product Requirements Document.
- System Design and Architecture.
- Final Team Work Allocation.
- Individual Work Assignment Guides.
- API contracts.
- Testing and evaluation reports.
- Deployment and recovery runbooks.

Repository links will be added once these files are uploaded.

## Local Setup

The application scaffold has not been created yet.

Installation, environment configuration and startup commands will be documented after the initial setup is working. Avoid treating placeholder commands as a runnable setup guide.

## Contribution Workflow

1. Clone the repository.
2. Create a feature branch for your task.
3. Work within your assigned module.
4. Coordinate shared schema, API or UI changes with the owner.
5. Run the checks relevant to your changes.
6. Open a pull request with:
   - What changed.
   - How it was verified.
   - Any remaining limitations.
7. Merge after review and required checks.

Suggested branch names:

- `feature/student-dashboard`
- `feature/moderation-panel`
- `feature/experience-api`
- `feature/background-worker`
- `feature/hybrid-retrieval`
- `feature/roadmap-logic`

Never commit credentials, private student data or real verification documents.

## License

A license has not been selected yet. The team will decide the usage and distribution terms before a public release.
