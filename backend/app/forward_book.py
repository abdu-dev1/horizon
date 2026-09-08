"""
The "forward book" window — decided renewal months that CAN BE reclassified from
training history to the upcoming Book of Business, for a genuine leak-free forward
test. Currently EMPTY by explicit choice: every decided row trains the model and
always shows in the Renewal Database, with nothing held out and nothing that can
disappear from view. (2026-07/08 were held out earlier for forward-testing; rolled
back into normal history on request.)

A month listed here would have real, settled outcomes, but held out of everything
that would make scoring it circular:

  * model training      (train_real.py)      -> excluded, so scoring these
                                                 renewals is a genuine, leak-free
                                                 forward test;
  * the leak flag        (build_book.py)      -> these rows don't count as
                                                 "trained on", so their renewals
                                                 are NOT flagged leaked and DO
                                                 surface on the Upcoming Renewals
                                                 page (with the real outcome shown
                                                 next to the prediction);
  * the Renewal Database (data_quality.py)    -> hidden, because they're the
                                                 current book, not past history.

The underlying rows stay in real_history.csv either way. To hold a month out again
for a genuine forward test, add it back here and retrain.
"""

from __future__ import annotations

import pandas as pd

# Decided renewal months reclassified from "history" to "upcoming book". Empty ->
# nothing held out, nothing hidden from the Renewal Database, ever.
FORWARD_BOOK_MONTHS: frozenset[str] = frozenset()


def forward_mask(eff_dates) -> pd.Series:
    """Boolean mask, True where an eff_date falls in a forward-book month."""
    m = pd.to_datetime(eff_dates, errors="coerce").dt.to_period("M").astype(str)
    return m.isin(FORWARD_BOOK_MONTHS)
