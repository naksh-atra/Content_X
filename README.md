# Content_X — @SatyaNaaksh X Content Pipeline

Automated content generation pipeline for @SatyaNaaksh with builder-focused identity.

## Architecture

```
Task Scheduler (3x daily)
    ↓
collector.py → dump/{morning|noon|evening}_dump.txt
    ↓
v3_post_bot.py → Generation + Selection + Routing
    ↓
Telegram Bot API → Channel posts
    ↓
archive/posted_log.txt
```

## Content Sources

| Batch | Sources | Output Character |
|-------|---------|-----------------|
| Morning | TechCrunch, ET CIO RSS | Broad tech news → QT/replies |
| Noon | Hacker News, Reddit | Developer-focused → Mix |
| Evening | Reddit | Tech news → QT/replies |
| Experiments | experiments_inbox/ | Builder originals (manual) |

## Post Types

| Type | Source | Threshold | Visual |
|------|--------|-----------|--------|
| Experiment original | experiments_inbox/ | 25 | Preferred |
| Dump original | dump/ files | 40 | Optional |
| QT/reply | Any source | 10 | Text-first |
| Thread | Any source | 20 | Optional |

## Visual Policy

- `VISUAL_REQUIRED_FOR_ORIGINAL = False` — text-only strong originals allowed
- `VISUAL_PREFERRED_FOR_EXPERIMENT_ORIGINAL = True` — visual preferred, not required

## Runtime Flags

```python
SHADOW_MODE = False    # Production mode
DRY_RUN = False       # Live sending enabled
ENABLE_THREADS = False # Threads optional
ENABLE_DUMP_ORIGINALS = True
```

## Usage

### Collection (via Task Scheduler)

```bash
python collector.py morning
python collector.py noon
python collector.py evening
```

### Generation

```bash
# Dry run (test without sending)
python v3_post_bot.py --dry-run

# Production
python v3_post_bot.py
```

## Experiment Workflow

Drop folders into `experiments_inbox/`:
```
experiments_inbox/exp_001_YYYYMMDD/
├── meta.txt      # title, date, status, tags
├── notes.txt    # raw observations
└── screenshots/  # optional images
```

## Files

| File | Purpose |
|------|---------|
| v3_post_bot.py | Main generation pipeline |
| v3_utils.py | Shared utilities |
| collector.py | Content collection |
| process_builder.txt | Original post prompt |
| process_reply_qt.txt | QT/reply prompt |
| sources.json | Collection sources config |

## Future (v4)

Source-layer realignment after 2-4 weeks live stabilization:
1. Dev tool/changelogs → Builder originals
2. HN AI agents/coding tools → Reply/QT discovery
3. Selected builder X accounts → Discourse drafts