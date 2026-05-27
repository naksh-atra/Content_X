# Content_X — @SatyaNaaksh

## Structure

```
content_x/
├── .env                  # API keys (shared config)
├── .gitignore
├── README.md
├── legacy/               # Discontinued workflows
│   ├── collector/        # Data collection pipeline
│   │   ├── collector.py
│   │   ├── sources.json
│   │   ├── seen_urls.json
│   │   ├── resolve_channels.py
│   │   ├── schedule_*.ps1
│   │   └── dump/         # Collected output
│   ├── v2/               # v2 post bot
│   │   ├── post_bot.py
│   │   ├── process.txt
│   │   └── process_mini.txt
│   ├── v3/               # v3 post bot
│   │   ├── v3_post_bot.py
│   │   ├── v3_utils.py
│   │   ├── process_builder.txt
│   │   ├── process_reply_qt.txt
│   │   ├── posted_log.txt
│   │   ├── v3_plan.md
│   │   └── experiments_inbox/
│   ├── plans/            # Archived plans
│   │   └── growth_pipeline_plan.md
│   ├── other/            # Other misc files
│   │   └── current.txt
│   └── archive/          # Old dump archives
└── ...                   # (new project root)
```

All previous workflows (v2, v3, collector) are discontinued and moved to `legacy/`.

Root level is clean for new development.
