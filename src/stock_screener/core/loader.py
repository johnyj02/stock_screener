"""
Dynamic loader for screening strategies.
Automatically discovers strategy classes in the strategies/ directory.
"""
import os
import pkgutil
import importlib
import inspect
import logging
import threading
from typing import List, Type, Optional
from stock_screener.core.strategy import BaseStrategy

logger = logging.getLogger(__name__)

_STRATEGY_CLASS_CACHE: Optional[List[Type[BaseStrategy]]] = None
_STRATEGY_CACHE_LOCK = threading.Lock()

class StrategyLoader:
    def __init__(self, strategies_package: str = "stock_screener.strategies"):
        self.strategies_package = strategies_package
        
    def _instantiate(self, classes: List[Type[BaseStrategy]]) -> List[BaseStrategy]:
        strategies: List[BaseStrategy] = []
        for cls in classes:
            try:
                strategies.append(cls())
            except Exception as e:
                logger.error(f"Failed to instantiate strategy '{cls.__name__}': {e}")
        return strategies

    def load_strategies(self) -> List[BaseStrategy]:
        """
        Scan the strategies package and return instances of all found strategy classes.
        """
        global _STRATEGY_CLASS_CACHE
        disable_cache = str(os.environ.get("STRATEGY_LOADER_DISABLE_CACHE", "")).lower() in {
            "1",
            "true",
            "yes",
        }
        if not disable_cache:
            with _STRATEGY_CACHE_LOCK:
                if _STRATEGY_CLASS_CACHE is not None:
                    return self._instantiate(_STRATEGY_CLASS_CACHE)

        strategies: List[BaseStrategy] = []
        strategy_classes: List[Type[BaseStrategy]] = []
        
        # Find the package directory
        try:
            package = importlib.import_module(self.strategies_package)
        except ImportError as e:
            logger.error(f"Could not import strategies package '{self.strategies_package}': {e}")
            return []
            
        package_path = os.path.dirname(package.__file__)
        logger.info(f"Scanning for strategies in: {package_path}")
        
        # Iterate over all modules in the package directory
        for _, module_name, _ in pkgutil.iter_modules([package_path]):
            full_module_name = f"{self.strategies_package}.{module_name}"
            try:
                module = importlib.import_module(full_module_name)
                
                # Inspect module for classes that inherit from BaseStrategy
                for name, cls in inspect.getmembers(module, inspect.isclass):
                    # Check if it's a subclass of BaseStrategy, but NOT BaseStrategy itself
                    if issubclass(cls, BaseStrategy) and cls is not BaseStrategy:
                        strategy_classes.append(cls)
                        logger.debug(f"Loaded strategy: {name}")
                            
            except Exception as e:
                logger.error(f"Failed to load module '{full_module_name}': {e}")

        strategies = self._instantiate(strategy_classes)
        if not disable_cache:
            with _STRATEGY_CACHE_LOCK:
                _STRATEGY_CLASS_CACHE = strategy_classes
        logger.info(f"Total strategies loaded: {len(strategies)}")
        return strategies
