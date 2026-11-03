# What to ask with the sample files

These files are not a script of questions and answers. In demo mode the assistant quotes sentences from the documents on the claim. If the sentence is not there, or the overlap with the question is weak, it says the evidence is not enough and does not call a model.

Upload both files to the same claim, for example `SIN-1001`:

- `home-policy.pdf` — Ana Ruiz's home policy, two pages.
- `endorsement-clause-4.docx` — the 15/06/2024 endorsement that amends clause 4.

Wait until both say **ready**. The twelve questions below are the ones in `backend/eval/dataset.json`. Any other question is fine when the answer is written in one of the two files.

## What is written

Policy, page 1: number `POL-NS-2024-1187`, insured Ana Ruiz, term `01/03/2024` to `01/03/2025`, water-damage limit `2000 EUR`, deductible `150 EUR`.

Policy, page 2: damage from lack of maintenance is not covered. The claim must be reported within `7 days`.

Endorsement: date `15/06/2024`, the limit changes to `5000 EUR`, and the sentence "The deductible does not change."

The two limits are deliberate. They show the conflict and the rule that the later document prevails.

## Questions that should be answered

| Question | What must appear | Why |
| --- | --- | --- |
| What is the policy number | `POL-NS-2024-1187`, citation to `home-policy.pdf` p. 1 | That sentence is on the first page of the policy. |
| Who is the insured person | `Ana Ruiz`, citation to the policy p. 1 | The insured person is named only there. |
| What is the policy term | `01/03/2024`, citation to the policy p. 1 | The term is on the same page. The citation can cover more than one sentence from that page. |
| What is the indemnity limit for water damage | `2000`, `5000`, and `prevails`. Citations to the policy p. 1 and the endorsement p. 1 | Both amounts are written. The "Conflicting documents" banner appears because there are two limits in EUR. The endorsement prevails because it is later (`15/06/2024`). Summary confidence is `medium`. |
| What was the original water damage limit before the endorsement | `2000`, citation to the policy p. 1 | The question asks for the policy amount. The warning can also appear, because the question is about that limit. |
| What is the deductible for water damage | `150`, citation to the policy p. 1 | The policy sets 150 EUR. The endorsement does not state a second amount, so there is no deductible conflict. |
| Does the deductible change with the endorsement | `does not change`, citation to the endorsement p. 1 | The endorsement says that. |
| What damage is excluded for lack of maintenance | `lack of maintenance`, citation to the policy p. 2 | The exclusion is in clause 7, page 2. |
| How long does the insured have to report the claim | `7 days`, citation to the policy p. 2 | The deadline is in clause 12, page 2. |
| What is the date of the endorsement | `15/06/2024`, citation to the endorsement p. 1 | It is the only date on the endorsement. |

On a valid answer, **View excerpt** opens the cited chunk: file, page, and the retrieved text. The card under the answer shows the policy number, the dates that were found, and the applicable coverage.

## Questions that should be refused

| Question | What you should see | Why |
| --- | --- | --- |
| Does it cover jewelry theft at the airport | "Not enough evidence. No answer was invented." No citation that supports a yes. | Jewelry and the airport are not in either file. |
| Who is the surveyor assigned to the fire | The same insufficient-evidence banner. | There is no surveyor and no fire in the documents. |

The summary of a refusal can still show the policy number if that fact was retrieved, but applicable coverage stays empty and confidence stays `low`.
