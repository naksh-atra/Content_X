# Personal Growth Pipeline Plan — AI Dev Account

## Overview
A parallel pipeline to create longer, meaningful X posts (threads, case studies) without interfering with the existing news-digest pipeline.

---

## 1. Growth Foundations for ~10-Follower AI-Dev Account

| What Works | Why It Moves the Needle | How to Codify |
|------------|------------------------|---------------|
| **Consistent visual branding** | Feed looks cohesive, higher follow rate | Pick static banner + 2-color palette |
| **Signature rhythm** | Algorithm rewards regular activity | Post at same 3 times daily (when ready) |
| **Micro-CTA at end of threads** | Turns readers into participants | Auto-append 2-sentence CTA block |
| **Thread length 3-6 tweets** | Optimal completion rate | Stop at 5 tweets unless manually extended |
| **Reply-to-self updates** | Keeps conversation open | Schedule "result tweet" 24h later |

---

## 2. High-Level Architecture (Parallel Pipeline)

```
┌─────────────────┐      ┌───────────────────────┐
│  Source Store   │      │   Experiment Vault    │
│  (your notes)   │      │  experiments_inbox/   │
└───────┬─────────┘      └───────┬───────────────┘
        │                          │
        ▼                          ▼
   ┌─────────────────┐    ┌───────────────────────┐
   │   Content-Prep │    │   Scoring & Routing  │
   └───────┬─────────┘    └───────┬───────────────┘
           │                      ▼
           ▼                ┌───────────────────────┐
      ┌───────────────┐       │   Thread Builder      │
      │   Generator   │       │   (4-5 tweet skeleton)│
      └───────┬───────┘       └───────┬───────────────┘
              │                       │
          ┌───────┴───────┐       ┌───────┴───────┐
          │   Validation  │       │   CTA Injection│
          └───────┬───────┘       └───────┬───────┘
                  │                       ▼
                  ▼                ┌───────────────────────┐
          ┌───────────────┐        │   Telegram Sender   │
          │   Dry-Run     │        │   (dry-run first)  │
          │   (txt dump)  │        └───────┬───────────────┘
          └───────┬───────┘                │
                  │                        ▼
                  ▼               ┌───────────────────────┐
          ┌─────────────────┐      │   Post-Log Append   │
          │   Manual Review │      │   (posted_log.txt)   │
          └─────────────────┘      └───────────────────────┘
```

---

## 3. Experiment Vault Structure

```
/personal_experiments/
   2026-10-25_exp_grok-suggests-thread/
   ├─ meta.txt          # title, date, status, tags
   ├─ notes.txt         # step-by-step description
   ├─ screenshots/      # optional proof
   ├─ results/          # benchmark numbers
   └─ voice.md          # tone guidelines
```

---

## 4. Thread Generation Workflow

1. **Folder Scan** – list newest `meta.txt` folders
2. **Scoring** – compute first-party strength (high/medium/low)
3. **Lane-Keyword Filter** – must match ≥2 keywords
4. **Visual Confirmation** – check visual requirements
5. **Thread Builder Prompt** – LLM generates 4-5 tweet thread
6. **CTA Injection** – append call-to-action
7. **Dry-Run Save** – write to `personal_posts/dry/`
8. **Manual Review** – quick sanity check
9. **Publish** – send to X + schedule follow-up
10. **Metrics Capture** – track impressions/engagement

---

## 5. Separation From Existing Pipeline

| Aspect | Existing Pipeline | New Pipeline |
|--------|------------------|--------------|
| **Source** | RSS feeds, public news | Your experiment vault |
| **Content Type** | 1-2 sentence snippets | 4-5 tweet threads |
| **Scoring** | Freshness & dump strength | First-party strength |
| **Output** | Shared archive/ logs | Own `personal_posts/` folder |
| **Automation** | Fully autonomous | Semi-autonomous (manual check) |

---

## 6. Next Steps

1. Map your Grok recommendations to this framework
2. Tailor lane-keywords to your specific topics
3. Set up skeleton scripts (scan, score, builder)
4. Create first experiment folder template

---

## 7. Your Input Needed

- Visual style preferences?
- Topics/keywords for "in-lane"?
- Tone constraints beyond dry humor?
- Preferred posting cadence?

---

Ready to implement once you provide your Grok suggestions and preferences!