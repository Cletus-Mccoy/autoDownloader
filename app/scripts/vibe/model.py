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
# The piece model (PieceModel below) has its own settings; this is the
# one-vector-per-track model.


def build():
    return make_pipeline(
        Normalizer(),
        LogisticRegression(C=C, max_iter=4000, class_weight="balanced"),
    )


def out_of_fold_proba(X, y, folds=5, seed=None, build=None):
    """Class probabilities where every prediction comes from an unseen fold.

    Returns (proba, classes). Used for honest evaluation and for spotting
    tracks whose own playlist scores badly — a track's prediction never sees
    that track during training. build defaults to this module's build(); pass
    piece_build for the piece model.
    """
    if seed is None:
        seed = config.RANDOM_SEED
    if build is None:
        build = globals()["build"]
    smallest = int(np.bincount(np.unique(y, return_inverse=True)[1]).min())
    folds = max(2, min(folds, smallest))
    cv = StratifiedKFold(n_splits=folds, shuffle=True, random_state=seed)
    proba = cross_val_predict(build(), X, y, cv=cv, method="predict_proba")
    return proba, np.unique(y)


# ── The piece model: several vectors per track, plus who made it ───────────
#
# A track is a Track(pieces, artists): its (n, 1280) piece vectors (see
# pieces.py) and its set of artist names. X is a 1-D object array of them, so
# sklearn's CV machinery splits tracks, never pieces of one track across folds.

import re
from collections import namedtuple

from sklearn.base import BaseEstimator, ClassifierMixin
from sklearn.preprocessing import StandardScaler

Track = namedtuple("Track", "pieces artists")

PIECE_C = 1.0          # measured: with per-piece rows, C=1 beat C=10
ARTIST_WEIGHT = 1.0    # measured: weight 1 beat 0.5
ARTIST_ALPHA = 1.0     # add-one smoothing of the artist counts

_ARTIST_SPLIT = re.compile(r",|&| feat\.? | ft\.? | x | vs\.? | and ")


def artists_of(track):
    """Lower-cased artist names; a collaboration counts for each artist."""
    raw = (track.get("artist") or "").lower()
    return frozenset(p.strip() for p in _ARTIST_SPLIT.split(raw) if p.strip())


def track_inputs(vectors, tracks):
    """Object array of Track for the tracks that have piece vectors."""
    X = np.empty(len(tracks), dtype=object)
    for i, t in enumerate(tracks):
        X[i] = Track(np.atleast_2d(vectors[t["videoId"]]), artists_of(t))
    return X


class PieceModel(BaseEstimator, ClassifierMixin):
    """Logistic regression on every piece; a track's score is the geometric
    mean of its pieces' scores (pieces must agree), times an artist prior
    learned from the training tracks:

        p(c) ∝ p_audio(c) * ((n_c + alpha) / (n + alpha * K)) ** weight

    n_c counts training tracks sharing an artist with this track in playlist
    c. With no known artist the prior is flat and the audio decides alone.
    """

    def __init__(self, C=PIECE_C, artist_weight=ARTIST_WEIGHT, artist_alpha=ARTIST_ALPHA):
        self.C, self.artist_weight, self.artist_alpha = C, artist_weight, artist_alpha

    def fit(self, X, y):
        y = np.asarray(y)
        rows = np.vstack([t.pieces for t in X])
        labels = np.repeat(y, [len(t.pieces) for t in X])
        self.audio_ = make_pipeline(
            StandardScaler(), Normalizer(),
            LogisticRegression(C=self.C, max_iter=4000, class_weight="balanced"),
        ).fit(rows, labels)
        self.classes_ = self.audio_.classes_
        col = {c: i for i, c in enumerate(self.classes_)}
        self.artist_counts_ = {}
        for t, label in zip(X, y):
            for a in t.artists:
                self.artist_counts_.setdefault(a, np.zeros(len(self.classes_)))[col[label]] += 1
        return self

    def predict_proba(self, X):
        sizes = [len(t.pieces) for t in X]
        piece_p = self.audio_.predict_proba(np.vstack([t.pieces for t in X]))
        out = np.empty((len(X), len(self.classes_)))
        k = len(self.classes_)
        start = 0
        for i, (t, n) in enumerate(zip(X, sizes)):
            lp = np.log(np.clip(piece_p[start:start + n], 1e-9, 1)).mean(0)
            start += n
            p = np.exp(lp - lp.max())
            if self.artist_weight:
                counts = sum((self.artist_counts_.get(a, 0) for a in t.artists), np.zeros(k))
                if counts.sum():
                    p = p * ((counts + self.artist_alpha)
                             / (counts.sum() + self.artist_alpha * k)) ** self.artist_weight
            out[i] = p / p.sum()
        return out

    def predict(self, X):
        return self.classes_[self.predict_proba(X).argmax(1)]


def piece_build():
    return PieceModel()
