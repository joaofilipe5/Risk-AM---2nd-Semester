import numpy as np
import pandas as pd
import logging
from datetime import datetime, timedelta
import yfinance as yf
from tqdm import tqdm
#
from ClassStock import Stock

class Portfolio:
    """
    Represents a stock portfolio, computing risk metrics and allowing simulations.
    """

    def __init__(self, stocks, risk_free_rate, initial_cash):
        self.stocks = stocks  # dictionary: { "Symbol": Stock(...) }
        self.RF = risk_free_rate
        self.cash_balance = initial_cash
        self.weights = self.getWeights()

        self.portfolio_returns = None
        self.portfolio_excess_returns = None
        self.market_close = None
        self.simulated_portfolio_values = None
        self.simulated_metrics = None

    def getWeights(self):
        """
        Calculates the fraction of each stock in the total portfolio (based on self.stocks[sym].value).
        """
        total_value = sum(st.getAmount() for st in self.stocks.values())
        if total_value == 0:
            logging.error("Total portfolio value is zero.")
            raise ValueError("Total portfolio value is zero.")

        w = {}
        for symbol, st in self.stocks.items():
            w[symbol] = st.getAmount() / total_value
        return w

    def update_stock(self, symbol, quantity, price, increase=True):
        """
        Updates the monetary position for a given asset (buy or sell).
        """
        if symbol not in self.stocks:
            if not increase:
                raise ValueError(f"Unable to sell {symbol} that is not in the portfolio.")
            self.stocks[symbol] = Stock(symbol, invested=0.0, value=0.0, risk_free_rate=self.RF)

        stock = self.stocks[symbol]
        current_value = stock.getAmount()

        if increase:
            # buy
            stock.value = current_value + (quantity * price)
            stock.investment += (quantity * price)
        else:
            # sell
            if stock.value < (quantity * price):
                raise ValueError(f"Insufficient value in {symbol} to sell.")
            stock.value = current_value - (quantity * price)
            if stock.value == 0:
                del self.stocks[symbol]

        # recalculate weights
        if len(self.stocks) > 0:
            self.weights = self.getWeights()
        else:
            self.weights = {}

        logging.info(f"Update of {symbol}: qty={quantity}, price={price}, increase={increase}")

    def has_stock(self, symbol, quantity):
        """
        Checks if the portfolio has sufficient value/quantity to sell X shares,
        based on the last price (approx).
        """
        if symbol not in self.stocks:
            return False
        st = self.stocks[symbol]
        c = st.getClose()
        last_price = c.iloc[-1] if not c.empty else 0
        return st.getAmount() >= (quantity * last_price)

    def getAdjClosePrices(self):
        """
        DataFrame with 'Adj Close' for each stock, columns=symbol, index=date.
        """
        data = pd.DataFrame()
        for st in self.stocks.values():
            c = st.getClose()
            if not c.empty:
                data[st.symbol] = c
            else:
                logging.warning(f"No closing prices for {st.symbol}.")
        return data

    def getPortfolioAdjustedClosePrices(self):
        """
        Weighted sum of 'Adj Close' to simulate buy-and-hold.
        """
        adj_close = self.getAdjClosePrices()
        if adj_close.empty:
            logging.warning("No closing prices to compose the portfolio.")
            return pd.Series(dtype=float)

        w = pd.Series(self.weights)
        weighted = adj_close.mul(w, axis=1)
        return weighted.sum(axis=1)

    def getReturns(self):
        """
        Daily returns of each stock (pct_change) + 'Portfolio' column.
        """
        prices = self.getAdjClosePrices()
        if prices.empty:
            logging.warning("No price data to calculate returns.")
            return pd.DataFrame()

        stock_returns = prices.pct_change()
        p_return_series = self.getPortfolioReturns()
        stock_returns["Portfolio"] = p_return_series
        return stock_returns

    def getAdjustedReturns(self):
        """
        If any stock has adjusted returns, merges everything and does a dot with weights
        to get the portfolio return series.
        """
        df = pd.DataFrame()
        for st in self.stocks.values():
            r = st.getAdjustedReturns()
            if not r.empty:
                df[st.symbol] = r
        if df.empty:
            logging.warning("No adjusted returns for the portfolio.")
            return pd.Series(dtype=float)

        w = pd.Series(self.weights)
        return df.dot(w)

    def getPortfolioReturns(self):
        """
        Daily returns for the portfolio (cached if already calculated).
        """
        if self.portfolio_returns is not None:
            return self.portfolio_returns

        r = self.getAdjustedReturns()
        if r.empty:
            self.portfolio_returns = pd.Series(dtype=float)
            return self.portfolio_returns

        self.portfolio_returns = r
        return self.portfolio_returns

    def getPortfolioExcessReturns(self):
        """
        Portfolio return - daily risk-free rate.
        """
        if self.portfolio_excess_returns is not None:
            return self.portfolio_excess_returns
        pr = self.getPortfolioReturns().dropna()
        if pr.empty:
            self.portfolio_excess_returns = pd.Series(dtype=float)
            return self.portfolio_excess_returns

        daily_rf = (1 + self.RF) ** (1/252) - 1
        self.portfolio_excess_returns = pr - daily_rf
        return self.portfolio_excess_returns

    def getPortfolioSharpe(self):
        er = self.getPortfolioExcessReturns().dropna()
        std_dev = er.std()
        if std_dev == 0 or np.isnan(std_dev):
            return np.nan
        return er.mean() / std_dev * np.sqrt(252)

    def getPortfolioSortino(self):
        er = self.getPortfolioExcessReturns().dropna()
        negative = er[er < 0]
        if negative.empty or negative.std() == 0:
            return np.nan
        return er.mean() / negative.std() * np.sqrt(252)

    def getPortfolioStdDev(self):
        rr = self.getPortfolioReturns().dropna()
        if rr.empty:
            return np.nan
        return rr.std() * np.sqrt(252)

    def getPortfolioCovarianceMatrix(self):
        """
        Covariance matrix of the daily returns of each stock.
        """
        df = pd.DataFrame()
        for st in self.stocks.values():
            r = st.getAdjustedReturns()
            if not r.empty:
                df[st.symbol] = r
        df.dropna(inplace=True)
        if df.empty:
            return pd.DataFrame()
        return df.cov()

    def getCorrelationMatrix(self):
        """
        Correlation matrix of the daily returns of each stock.
        """
        df = pd.DataFrame()
        for st in self.stocks.values():
            r = st.getAdjustedReturns()
            if not r.empty:
                df[st.symbol] = r
        df.dropna(inplace=True)
        if df.empty:
            return pd.DataFrame()
        return df.corr()

    def getMarginalContributionToRisk(self):
        """
        Marginal Contribution to Risk of each asset, based on covariance.
        """
        cov = self.getPortfolioCovarianceMatrix()
        if cov.empty:
            return pd.Series(dtype=float)
        w = np.array([self.weights[sym] for sym in self.stocks])
        port_daily_vol = self.getPortfolioStdDev() / np.sqrt(252)
        if port_daily_vol == 0 or np.isnan(port_daily_vol):
            return pd.Series(dtype=float)
        mctr_ = cov.dot(w) / port_daily_vol
        return pd.Series(mctr_, index=self.stocks.keys())

    def getComponentContributionToRisk(self):
        """
        Contribution to Risk = Weight * Marginal Contribution to Risk
        """
        mctr = self.getMarginalContributionToRisk()
        w_s = pd.Series(self.weights)
        return w_s * mctr

    def simulatePortfolioWithoutAsset(self, symbol):
        """
        Simulates the portfolio's volatility by removing an asset.
        """
        if symbol not in self.weights:
            return np.nan
        new_stocks = {s: obj for s, obj in self.stocks.items() if s != symbol}
        if not new_stocks:
            return np.nan
        from ClassPortfolio import Portfolio
        testp = Portfolio(new_stocks, self.RF, self.cash_balance)
        return testp.getPortfolioStdDev()

    def getAssetImpactOnVolatility(self, symbol):
        """
        The difference in the portfolio's volatility when removing an asset.
        """
        orig = self.getPortfolioStdDev()
        new_ = self.simulatePortfolioWithoutAsset(symbol)
        if np.isnan(new_):
            return np.nan
        return orig - new_

    def getMaxDrawdown(self):
        pr = self.getPortfolioReturns().dropna()
        if pr.empty:
            return np.nan
        cum = (1 + pr).cumprod()
        peak = cum.expanding().max()
        dd = (cum / peak) - 1
        return dd.min()

    def getVaR(self, cl=0.05):
        """
        Value at Risk based on the percentile.
        """
        pr = self.getPortfolioReturns().dropna()
        if pr.empty:
            return np.nan
        return np.percentile(pr, cl * 100)

    def getCVaR(self, cl=0.05):
        """
        Conditional VaR (ES).
        """
        pr = self.getPortfolioReturns().dropna()
        if pr.empty:
            return np.nan
        var_ = self.getVaR(cl)
        return pr[pr <= var_].mean()

    def getMarketData(self, symbol='^GSPC'):
        """
        Downloads the market index to calculate Beta.
        """
        end_ = datetime.today()
        start_ = end_ - timedelta(days=365)
        try:
            df = yf.download(symbol, start=start_, end=end_, interval='1d', progress=False, auto_adjust=False)
            if df.empty:
                self.market_close = pd.Series(dtype=float)
            else:
                if isinstance(df.columns, pd.MultiIndex):
                    df.columns = df.columns.get_level_values(0)
                self.market_close = df['Adj Close']
        except Exception as e:
            logging.error(f"Error fetching market data {symbol}: {e}")
            self.market_close = pd.Series(dtype=float)
        return self.market_close

    def getMarketReturns(self):
        """
        Daily returns of the market (e.g.: ^GSPC).
        """
        if self.market_close is None:
            self.getMarketData()
        if self.market_close.empty:
            return pd.Series(dtype=float)
        return self.market_close.pct_change()

    def getPortfolioBeta(self):
        pr = self.getPortfolioReturns().dropna()
        mr = self.getMarketReturns().loc[pr.index].dropna()
        df = pd.DataFrame({'p': pr, 'm': mr}).dropna()
        if df.empty:
            return np.nan
        c = np.cov(df['p'], df['m'])
        pm = c[0][1]
        mm = c[1][1]
        if mm == 0:
            return np.nan
        return pm / mm

    def getTreynorRatio(self):
        b = self.getPortfolioBeta()
        if b == 0 or np.isnan(b):
            return np.nan
        ann_ret = self.getPortfolioReturns().mean() * 252
        return (ann_ret - self.RF) / b

    def getDetailedStockData(self):
        """
        Creates a DataFrame with detailed statistics for each stock in the portfolio.
        """
        rows = []
        for st in tqdm(self.stocks.values(), desc="Processing stocks"):
            sym = st.symbol
            beta = st.getBeta()
            sharpe = st.getSharpeRatio()
            sortino = st.getSortinoRatio()
            impact = self.getAssetImpactOnVolatility(sym)
            mctr = self.getMarginalContributionToRisk().get(sym, np.nan)
            ctr = self.getComponentContributionToRisk().get(sym, np.nan)

            try:
                c = st.getClose()
                cur_price = c.iloc[-1] if not c.empty else np.nan
                hi52 = c.max() if not c.empty else np.nan
                lo52 = c.min() if not c.empty else np.nan
            except:
                cur_price = hi52 = lo52 = np.nan

            tot_ret = st.getTotalReturn()
            beta_danger = abs(beta) > 1.5 if not np.isnan(beta) else False
            sharpe_danger = sharpe < 1 if not np.isnan(sharpe) else False
            sortino_danger = sortino < 1 if not np.isnan(sortino) else False

            rows.append({
                "Stock": sym,
                "Weight %": round(self.weights.get(sym, 0) * 100, 2),
                "Value $": round(st.getAmount(), 2),
                "Current Price": round(cur_price, 2) if not np.isnan(cur_price) else "N/A",
                "52-Week High": round(hi52, 2) if not np.isnan(hi52) else "N/A",
                "52-Week Low": round(lo52, 2) if not np.isnan(lo52) else "N/A",
                "Total Return %": round(tot_ret * 100, 2) if not np.isnan(tot_ret) else "N/A",
                "Beta": round(beta,4) if not np.isnan(beta) else "N/A",
                "Beta Danger": "Yes" if beta_danger else "No",
                "Sharpe": round(sharpe,4) if not np.isnan(sharpe) else "N/A",
                "Sharpe Danger": "Yes" if sharpe_danger else "No",
                "Sortino": round(sortino,4) if not np.isnan(sortino) else "N/A",
                "Sortino Danger": "Yes" if sortino_danger else "No",
                "Volatility Impact": round(impact,4) if not np.isnan(impact) else "N/A",
                "Contribution to Risk": round(ctr,6) if not np.isnan(ctr) else "N/A"
            })
        return pd.DataFrame(rows)

    def Summary(self):
        """
        Returns a dictionary with aggregate portfolio metrics (Sharpe, Sortino, Beta, etc.).
        """
        val = sum(st.getAmount() for st in self.stocks.values())
        stdv = self.getPortfolioStdDev()
        sh = self.getPortfolioSharpe()
        so = self.getPortfolioSortino()
        be = self.getPortfolioBeta()
        tr = self.getTreynorRatio()
        dd = self.getMaxDrawdown()
        var95 = self.getVaR(0.05)
        cvar95 = self.getCVaR(0.05)

        sharpe_danger = (sh < 1) if not np.isnan(sh) else False
        sortino_danger = (so < 1) if not np.isnan(so) else False
        beta_danger = (abs(be) > 1.5) if not np.isnan(be) else False
        dd_danger = (dd < -0.2) if not np.isnan(dd) else False
        var_danger = (var95 < -0.05) if not np.isnan(var95) else False
        cvar_danger = (cvar95 < -0.05) if not np.isnan(cvar95) else False

        return {
            "Portfolio Value": round(val,2),
            "Portfolio StdDev": round(stdv,4) if not np.isnan(stdv) else "N/A",
            "Portfolio Sharpe": round(sh,4) if not np.isnan(sh) else "N/A",
            "Sharpe Danger": "Yes" if sharpe_danger else "No",
            "Portfolio Sortino": round(so,4) if not np.isnan(so) else "N/A",
            "Sortino Danger": "Yes" if sortino_danger else "No",
            "Portfolio Beta": round(be,4) if not np.isnan(be) else "N/A",
            "Beta Danger": "Yes" if beta_danger else "No",
            "Portfolio Treynor": round(tr,4) if not np.isnan(tr) else "N/A",
            "Max Drawdown": round(dd,4) if not np.isnan(dd) else "N/A",
            "Max Drawdown Danger": "Yes" if dd_danger else "No",
            "VaR 95%": round(var95,4) if not np.isnan(var95) else "N/A",
            "VaR Danger": "Yes" if var_danger else "No",
            "CVaR 95%": round(cvar95,4) if not np.isnan(cvar95) else "N/A",
            "CVaR Danger": "Yes" if cvar_danger else "No"
        }

    def getDynamicTimeSeries(self, transaction_log=None, by_stock=False):
        """
        Returns a DataFrame with columns:
          Date, Cash, [Stock1, Stock2, ... if by_stock=True], PortfolioValue
        applying each transaction in chronological order.
        """
        if transaction_log is None or transaction_log.empty:
            logging.warning("No transaction_log or it is empty.")
            return pd.DataFrame()

        prices_df = self.getAdjClosePrices()
        if prices_df.empty:
            logging.warning("No prices to generate a dynamic time series.")
            return pd.DataFrame()

        all_dates = prices_df.index.sort_values().unique()
        transaction_log = transaction_log.sort_values("Date")

        if not pd.api.types.is_datetime64_any_dtype(transaction_log["Date"]):
            transaction_log["Date"] = pd.to_datetime(transaction_log["Date"])

        portfolio_values = []
        current_cash = self.cash_balance
        current_holdings = {}
        for sym, st in self.stocks.items():
            current_holdings[sym] = st.quantity

        for day in all_dates:
            day_txs = transaction_log[transaction_log["Date"] == day]

            for _, tx in day_txs.iterrows():
                ttype = tx["Type"].lower()
                tsym = tx["Symbol"]
                qty = tx["Quantity"]
                px = tx["Price"]

                if ttype == 'buy':
                    cost = qty * px
                    if current_cash < cost:
                        logging.error("Not enough cash to buy.")
                        continue
                    current_cash -= cost
                    current_holdings[tsym] = current_holdings.get(tsym, 0.0) + qty
                elif ttype == 'sell':
                    if current_holdings.get(tsym, 0.0) < qty:
                        logging.error("Not enough shares to sell.")
                        continue
                    revenue = qty * px
                    current_cash += revenue
                    current_holdings[tsym] -= qty
                    if current_holdings[tsym] <= 0:
                        del current_holdings[tsym]

            day_val = current_cash
            row_dict = {"Date": day, "Cash": current_cash}
            for s_, q_ in current_holdings.items():
                if s_ not in prices_df.columns:
                    continue
                if day not in prices_df.index:
                    continue
                px_today = prices_df.loc[day, s_]
                stock_val = q_ * px_today
                day_val += stock_val
                if by_stock:
                    row_dict[s_] = stock_val

            row_dict["PortfolioValue"] = day_val
            portfolio_values.append(row_dict)

        dyn_df = pd.DataFrame(portfolio_values)
        dyn_df.sort_values("Date", inplace=True)
        dyn_df.reset_index(drop=True, inplace=True)
        return dyn_df

    def monteCarloSimulation(self, num_simulations=1000, num_days=252):
        """
        Monte Carlo simulation at the portfolio level using GBM from the distribution of daily returns.
        """
        portfolio_returns = self.getPortfolioReturns().dropna()
        if portfolio_returns.empty:
            logging.warning("No portfolio returns for MC.")
            return pd.DataFrame()

        mean_ret = portfolio_returns.mean()
        std_dev = portfolio_returns.std()
        drift = mean_ret - 0.5 * (std_dev ** 2)
        dt = 1 / 252
        last_port_val = sum(st.getAmount() for st in self.stocks.values())

        sim_array = np.zeros((num_days, num_simulations))
        for i in range(num_simulations):
            np.random.seed(i)
            shocks = np.random.normal(0, std_dev, num_days)
            daily_ret = np.exp(drift * dt + shocks)
            port_path = last_port_val * np.cumprod(daily_ret)
            sim_array[:, i] = port_path

        if len(portfolio_returns.index) == 0:
            last_date = datetime.today()
        else:
            last_date = portfolio_returns.index[-1]
        future_dates = pd.bdate_range(start=last_date + pd.Timedelta(days=1), periods=num_days)

        sim_df = pd.DataFrame(
            sim_array,
            index=future_dates,
            columns=[f"Simulation_{i+1}" for i in range(num_simulations)]
        )
        self.simulated_portfolio_values = sim_df

        # Compute metrics
        ret_ = sim_df.pct_change().dropna(how='all')
        sharpe_ = ret_.mean(axis=0) / ret_.std(axis=0) * np.sqrt(252)
        negative_only = ret_[ret_ < 0]
        sortino_ = ret_.mean(axis=0) / negative_only.std(axis=0) * np.sqrt(252)
        mdd_ = sim_df.apply(lambda x: self.calculate_max_drawdown(x), axis=0)
        var_ = ret_.quantile(0.05, axis=0)
        cvar_ = ret_.apply(lambda x: x[x <= x.quantile(0.05)].mean(), axis=0)

        self.simulated_metrics = pd.DataFrame({
            "Simulation": sim_df.columns,
            "Sharpe Ratio": sharpe_.values,
            "Sortino Ratio": sortino_.values,
            "Max Drawdown": mdd_.values,
            "VaR 95%": var_.values,
            "CVaR 95%": cvar_.values
        })
        return sim_df

    @staticmethod
    def calculate_max_drawdown(series):
        cumret = (1 + series.pct_change()).cumprod()
        peak = cumret.expanding().max()
        dd = (cumret / peak) - 1
        return dd.min()

    def getSimulatedMetrics(self):
        """
        Returns the simulation metrics (Monte Carlo) of the portfolio.
        """
        if self.simulated_metrics is None:
            return pd.DataFrame()
        return self.simulated_metrics
