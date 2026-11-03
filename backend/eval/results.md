# Evaluation results

Fake embeddings and the extractive answerer, on `eval/dataset.json`.

- Recall@5: 1.00
- Grounding: 1.00
- Abstention accuracy: 1.00

| Question | Outcome | Recall@5 | Grounded | Abstain ok |
| --- | --- | --- | --- | --- |
| policy-number | answered | yes | yes | — |
| insured | answered | yes | yes | — |
| term | answered | yes | yes | — |
| limit | answered | yes | yes | — |
| original-limit | answered | yes | yes | — |
| deductible | answered | yes | yes | — |
| deductible-unchanged | answered | yes | yes | — |
| exclusion | answered | yes | yes | — |
| notice | answered | yes | yes | — |
| endorsement-date | answered | yes | yes | — |
| jewelry | insufficient_evidence | — | — | yes |
| surveyor | insufficient_evidence | — | — | yes |
