# Relevance labels (graded 0-3)

Computed from **ground truth the generator knows** (`truth.true_skills`, `truth.years`, `truth.education`),
never from the parsed profile, so parser noise does not leak into the labels.

Coverage = share of the JD's required skills the candidate truly has (implied skills count: Django implies Python).

| Grade | Rule |
|---|---|
| 3 | coverage >= 0.8 AND years >= min_years AND education >= min_education |
| 2 | coverage >= 0.6 AND (years OK OR education OK) |
| 1 | coverage >= 0.4 |
| 0 | otherwise |

"Relevant" for MRR / Recall / Precision means grade >= 2. NDCG uses gain 2^grade - 1.

**Known weakness:** the model's strongest features (required-skill coverage, years gap, education gap) are the
rubric's own inputs, so a high score is partly circular. See `docs/model_card.md`.
