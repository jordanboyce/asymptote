"""Financial analysis plugin for Asymptote.

Importing this package registers financial type extensions (currency, percent)
and a financial column-role detector with the generic StructuredStore.  After
import, any StructuredStore.create_table() call will automatically detect
currency/percent columns and map column names to financial roles.

Public surface:
  - AVAILABLE_METRICS      dict of metric_name → description
  - compute_financial_metric(store, identifier, metric, limit, group_by_symbol)
  - detect_financial_role(col_name, col_type)   (also registered as a hook)
  - _parse_number(val)     financial-aware number parser
"""

from services.financial.metrics import AVAILABLE_METRICS, compute_financial_metric
from services.financial.roles import detect_financial_role
from services.financial.type_hints import _parse_number

# Register type detectors (currency, percent) and role detector with the core store.
# Python's module system executes this block only once per interpreter session,
# so registration is inherently idempotent.
from services.financial import type_hints as _th
from services.financial import roles as _r

_th.register()
_r.register()

__all__ = [
    'AVAILABLE_METRICS',
    'compute_financial_metric',
    'detect_financial_role',
    '_parse_number',
]
