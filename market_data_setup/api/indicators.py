"""
Technical Indicators Module
Calculate indicators on-demand: EMA, RSI, ATR, MACD, Bollinger Bands
Using vectorized pandas/numpy for efficiency
"""

import pandas as pd
import numpy as np
from typing import List, Dict


class TechnicalIndicators:
    """Calculate technical indicators efficiently using vectorized operations"""

    def __init__(self, df: pd.DataFrame):
        """
        Initialize with OHLCV dataframe

        Args:
            df: DataFrame with columns [open, high, low, close, volume]
        """
        self.df = df.copy()
        self.df = self.df.reset_index(drop=True)

    def calculate_ema(self, periods: List[int] = None) -> pd.DataFrame:
        """
        Exponential Moving Average (trend following)

        Args:
            periods: List of EMA periods to calculate (default: [10, 21, 50, 200])

        Returns:
            DataFrame with EMA columns added
        """
        if periods is None:
            periods = [10, 21, 50, 200]

        for period in periods:
            self.df[f'ema_{period}'] = self.df['close'].ewm(
                span=period,
                adjust=False
            ).mean()

        return self.df

    def calculate_atr(self, period: int = 14) -> pd.DataFrame:
        """
        Average True Range (volatility indicator)

        Args:
            period: Lookback period (default: 14)

        Returns:
            DataFrame with 'atr' column added
        """
        high_low = self.df['high'] - self.df['low']
        high_close = np.abs(self.df['high'] - self.df['close'].shift())
        low_close = np.abs(self.df['low'] - self.df['close'].shift())

        ranges = pd.concat([high_low, high_close, low_close], axis=1)
        true_range = np.max(ranges.values, axis=1)

        self.df['atr'] = pd.Series(true_range).rolling(window=period).mean().values

        return self.df

    def calculate_rsi(self, period: int = 14) -> pd.DataFrame:
        """
        Relative Strength Index (momentum)
        Range: 0-100 (>70 overbought, <30 oversold)

        Args:
            period: Lookback period (default: 14)

        Returns:
            DataFrame with 'rsi_{period}' column added
        """
        delta = self.df['close'].diff()

        # Separate gains and losses
        gain = delta.where(delta > 0, 0).rolling(window=period).mean()
        loss = (-delta.where(delta < 0, 0)).rolling(window=period).mean()

        # Calculate RS and RSI
        rs = gain / loss
        rsi = 100 - (100 / (1 + rs))

        self.df[f'rsi_{period}'] = rsi

        return self.df

    def calculate_macd(self, fast: int = 12, slow: int = 26, signal: int = 9) -> pd.DataFrame:
        """
        MACD (Moving Average Convergence Divergence)
        Trend following + momentum indicator

        Args:
            fast: Fast EMA period (default: 12)
            slow: Slow EMA period (default: 26)
            signal: Signal line EMA period (default: 9)

        Returns:
            DataFrame with 'macd', 'macd_signal', 'macd_hist' columns
        """
        ema_fast = self.df['close'].ewm(span=fast, adjust=False).mean()
        ema_slow = self.df['close'].ewm(span=slow, adjust=False).mean()

        self.df['macd'] = ema_fast - ema_slow
        self.df['macd_signal'] = self.df['macd'].ewm(span=signal, adjust=False).mean()
        self.df['macd_hist'] = self.df['macd'] - self.df['macd_signal']

        return self.df

    def calculate_bollinger_bands(self, period: int = 20, std_dev: float = 2) -> pd.DataFrame:
        """
        Bollinger Bands (volatility and support/resistance)

        Args:
            period: SMA period (default: 20)
            std_dev: Number of standard deviations (default: 2)

        Returns:
            DataFrame with 'bb_upper', 'bb_middle', 'bb_lower' columns
        """
        sma = self.df['close'].rolling(period).mean()
        std = self.df['close'].rolling(period).std()

        self.df['bb_upper'] = sma + (std * std_dev)
        self.df['bb_middle'] = sma
        self.df['bb_lower'] = sma - (std * std_dev)

        return self.df

    def calculate_obv(self) -> pd.DataFrame:
        """
        On Balance Volume (volume-based momentum)
        Cumulative volume based on price direction

        Returns:
            DataFrame with 'obv' column added
        """
        obv = np.where(
            self.df['close'] > self.df['close'].shift(1),
            self.df['volume'],
            np.where(
                self.df['close'] < self.df['close'].shift(1),
                -self.df['volume'],
                0
            )
        )

        self.df['obv'] = pd.Series(obv).cumsum().values

        return self.df

    def calculate_volume_sma(self, period: int = 20) -> pd.DataFrame:
        """
        Volume Simple Moving Average (liquidity indicator)

        Args:
            period: Lookback period (default: 20)

        Returns:
            DataFrame with 'volume_sma' column added
        """
        self.df['volume_sma'] = self.df['volume'].rolling(period).mean()

        return self.df

    def calculate_stochastic(self, period: int = 14, smooth: int = 3) -> pd.DataFrame:
        """
        Stochastic Oscillator (overbought/oversold)

        Args:
            period: Lookback period (default: 14)
            smooth: Smoothing period for %K (default: 3)

        Returns:
            DataFrame with 'stoch_k' and 'stoch_d' columns
        """
        low_min = self.df['low'].rolling(period).min()
        high_max = self.df['high'].rolling(period).max()

        stoch_k = 100 * (self.df['close'] - low_min) / (high_max - low_min)
        self.df['stoch_k'] = stoch_k.rolling(smooth).mean()
        self.df['stoch_d'] = self.df['stoch_k'].rolling(smooth).mean()

        return self.df

    def calculate_all_standard(self) -> pd.DataFrame:
        """
        Calculate all standard indicators for backtesting
        EMA, ATR, RSI, MACD, Volume SMA

        Returns:
            DataFrame with all indicators
        """
        self.calculate_ema([10, 21, 50, 200])
        self.calculate_atr(14)
        self.calculate_rsi(14)
        self.calculate_macd(12, 26, 9)
        self.calculate_volume_sma(20)

        return self.df

    def get_data(self) -> pd.DataFrame:
        """Get the dataframe with all calculated indicators"""
        return self.df

    def to_dict(self, orient: str = 'records') -> dict:
        """
        Convert to dictionary format (for JSON response)

        Args:
            orient: pandas to_dict orientation (default: 'records')

        Returns:
            Dictionary representation of dataframe
        """
        return self.df.to_dict(orient=orient)

    def to_json(self, date_format: str = 'iso', orient: str = 'records') -> str:
        """
        Convert to JSON string

        Args:
            date_format: Date format (default: 'iso')
            orient: pandas to_json orient (default: 'records')

        Returns:
            JSON string
        """
        return self.df.to_json(orient=orient, date_format=date_format)
