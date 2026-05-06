"""Financial-domain helpers: column roles, currency/percent number parsing,
portfolio metrics, and the per-Collection HoldingsStore.

Public surface:
  - HoldingsStore                       per-Collection typed table store
  - AVAILABLE_METRICS                   dict of metric_name → description
  - compute_financial_metric(...)
  - detect_financial_role(...)
  - _parse_number(...)                  financial-aware number parser
"""

from services.financial.holdings_store import HoldingsStore
from services.financial.metrics import AVAILABLE_METRICS, compute_financial_metric
from services.financial.roles import detect_financial_role
from services.financial.type_hints import _parse_number

__all__ = [
    'HoldingsStore',
    'AVAILABLE_METRICS',
    'compute_financial_metric',
    'detect_financial_role',
    '_parse_number',
]
