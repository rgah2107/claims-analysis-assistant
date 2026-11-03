"""Synthetic claim file used by the evaluation script.

Norte Seguros is fictional. The policy and the later endorsement disagree on
the water-damage limit on purpose, so the contradiction behaviour is measurable.
"""

from __future__ import annotations

POLICY_PAGES = [
    "\n".join(
        [
            "NORTE SEGUROS",
            "Home insurance policy.",
            "Policy number: POL-NS-2024-1187.",
            "Insured: Ana Ruiz.",
            "Term: 01/03/2024 to 01/03/2025.",
            "CLAUSE 4. Water damage.",
            "The indemnity limit for water damage is 2000 EUR per claim.",
            "The deductible for water damage is 150 EUR.",
            "This coverage includes repair of interior pipes.",
            "It also includes direct material damage caused by water.",
            "The premium for this policy is paid annually.",
            "The broker does not change the coverages in this clause.",
        ]
    ),
    "\n".join(
        [
            "CLAUSE 7. Exclusions.",
            "Damage caused by lack of maintenance is not covered.",
            "CLAUSE 12. Notice period.",
            "The insured must report the claim within 7 days of becoming aware of it.",
            "Notice may be given in writing to the broker.",
        ]
    ),
]

LETTER_PARAGRAPHS = [
    "Endorsement.",
    "Date: 15/06/2024.",
    "Policy POL-NS-2024-1187.",
    "Clause 4 is amended.",
    "The indemnity limit for water damage is changed to 5000 EUR per claim.",
    "The deductible does not change.",
]
