---
name: devmem-remember
description: Use after solving something non-obvious in a repo (surprising failure cause, enforced convention, tricky data quirk) to save a short lesson that future sessions in any repo will recall.
---

# Save a lesson

Only when a future session would waste real effort re-discovering it. Write what the problem looked like and the fix, not the story:

```
devmem remember "<short title>" --problem "<symptom as the next person would see it>" --fix "<what to do>" [--tag <tag>]
```

Rules:

- One lesson per problem, under about 1,500 characters in total. Duplicates are merged automatically.
- No secrets, tokens or customer data.
- If devmem says it is a duplicate, do nothing more.
- Commit the new `.devmem/lessons/*.md` file with your PR: that is how the lesson reaches the user's hub and other repos.
- If an earlier recalled lesson was wrong or outdated, rate it down: `devmem feedback <id> useless --note "<why>"`.
