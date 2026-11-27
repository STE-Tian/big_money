"""Big Money V2 research layer.

P1 is the point-in-time spot feature store. Funding rates are features only.
P2 trains a logistic baseline, LightGBM, and an unlabeled HMM on those
features. It does not place orders, trade perpetual contracts, or change the
V1 backtest fill rules.
"""

FEATURE_SET_VERSION = "v2.p1"
MODEL_VERSION = "v2.p2"
