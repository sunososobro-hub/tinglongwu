## 17. dev merge gate
- `dev` is the fast lane, `main` the quality lane. A dev PR merges on section 14 Green: each required check not finished or missing -> skip this run; red -> no merge. g5 is off; the section 3 FAIL reasons still apply. Opus PASS only for protected / g4 paths.
- Only `test` red on the dev tip blocks anything (P0 after 2 h). The merge-gate Action and other dev check failures: `rules_patrol.md` section 17.
