"""Stable public facade for the research domain.

Implementation is grouped by responsibility while existing imports continue to use
``app.modules.research.service`` during the compatibility migration.
"""

from app.modules.research.backtesting import *
from app.modules.research.catalog import *
from app.modules.research.experiments import *
from app.modules.research.factors import *
from app.modules.research.strategy import *
