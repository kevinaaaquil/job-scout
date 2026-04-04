# Design Prompt

> For use with Google Stitch or similar design tools to generate portfolio banners/tiles.

### Product Summary
JobScout is an AI-powered automated job search dashboard. It runs scheduled searches across major job boards (Greenhouse, Workday, Lever, Ashby, SmartRecruiters), uses GPT-4o-mini to score each posting against a candidate profile on a 0-100 rubric, and delivers daily email digests of high-match opportunities. It's a self-hosted, single-user tool for developers who want to automate the tedious parts of job hunting.

### Core Features
- **AI Job Matching** -- GPT-4o-mini scores jobs across 4 dimensions: experience level, tech stack, role/domain fit, and location
- **Multi-board Search** -- Aggregates listings from Greenhouse, Workday, Lever, Ashby, and SmartRecruiters via Brave Search API
- **Cron Scheduling** -- Automated recurring searches with timezone-aware cron expressions
- **Email Digests** -- Color-coded HTML email summaries of matched and borderline jobs
- **Smart Deduplication** -- Tracks seen jobs in MongoDB to avoid re-scoring
- **Run History & Audit Trail** -- Full history of every search run with scores, reasoning, and status
- **Dark Dashboard UI** -- Tabbed interface for managing queries, schedules, profiles, and settings

### Tech Stack
Python (Flask), MongoDB, OpenAI API (GPT-4o-mini), Brave Search API, APScheduler, Docker, deployed via Dokku on GCP

### Color Palette
The app uses a dark-mode-first design language with a slate/blue palette:

| Role | Color | Hex |
|---|---|---|
| Background | Dark Slate | `#0f172a` |
| Surface | Slate 700 | `#1e293b` |
| Border | Slate 500 | `#475569` |
| Primary Text | Slate 100 | `#e2e8f0` |
| Secondary Text | Slate 400 | `#94a3b8` |
| Accent / CTA | Blue 500 | `#3b82f6` |
| Success | Green 500 | `#22c55e` |
| Warning | Yellow 500 | `#eab308` |
| Danger | Red 500 | `#ef4444` |

### Typography
System fonts: `-apple-system, BlinkMacSystemFont, 'Segoe UI', Inter, Roboto, sans-serif`. Monospace for inputs: `'SF Mono', 'Fira Code', monospace`.

### Branding
- The logo is text-based: **"Job"** in accent blue (`#3b82f6`) + **"Scout"** in white -- minimal, no icon
- Design philosophy: minimalist, data-focused, professional, no unnecessary decoration
- Status indicators use colored dots (green = complete, yellow with pulse animation = running, red = error)

### Visual Elements for Banner Design
- The dark slate background with blue accent is the core identity
- Color-coded job score badges (green for high match, yellow for borderline)
- The tabbed dashboard layout (Run History, Search Queries, Schedule, Candidate Profile, Settings)
- Job cards with left-border color indicators and floating score numbers
- The pulsing yellow status dot during active runs
- A clean, professional, developer-tool aesthetic -- think monitoring dashboards, not consumer apps

### Dimensions
1280x720px (16:9 ratio)

### Mood / Tone
Professional, automated, intelligent. The vibe is "set it and forget it" -- a tool that works in the background while you focus on other things. Think: dark IDE themes, CI/CD dashboards, developer productivity tools.
