"""The classifier configuration, in one place.

Chosen by the sweep (see scripts/vibe/sweep.py) rather than by taste:

  Normalizer   effnet embeddings are trained in cosine geometry, so projecting
               onto the unit sphere matters more than any other single choice.
               Without it, routable playlists dropped from 10/22 to 7/22.
  C=10         sklearn's default C=1 is badly over-regularised for 1280 dims;
               the useful window is 10-100 and C=10000 collapses to 0/22.
  balanced     playlist sizes span 16 to 456 tracks, so unweighted classes let
               the big playlists absorb everything.

Kept separate from probe/sweep so the router, the remodeller and any future
training step all fit the same thing.
"""

import numpy as np

from sklearn.linear_model import LogisticRegression
from sklearn.model_selection import StratifiedKFold, cross_val_predict
from sklearn.pipeline import make_pipeline
from sklearn.preprocessing import Normalizer

from . import config

C = 10.0


def build():
    return make_pipeline(
        Normalizer(),
        LogisticRegression(C=C, max_iter=4000, class_weight="balanced"),
    )


def out_of_fold_proba(X, y, folds=5, seed=None):
    """Class probabilities where every prediction comes from an unseen fold.

    Returns (proba, classes). Used for honest evaluation and for spotting
    tracks whose own playlist scores badly — a track's prediction never sees
    that track during training.
    """
    if seed is None:
        seed = config.RANDOM_SEED
    smallest = int(np.bincount(np.unique(y, return_inverse=True)[1]).min())
    folds = max(2, min(folds, smallest))
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    proba = cross_val_predict(build(), X, y, cv=cv, method="predict_proba")
    return proba, np.unique(y)
