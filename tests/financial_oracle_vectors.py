"""Hand-calculated audit vectors, independent of production result builders."""

INITIAL_COST = {
    "initial_capital": 100.0,
    "nav": [("2024-01-01", 90.0), ("2025-01-01", 90.0)],
    "total_return_pct": -10.0,
    "initial_year_return_pct": -10.0,
    "mdd_pct": -10.0,
}

BORROW_INTEREST = {
    "initial_capital": 100_000_000.0,
    "borrowed": 20_000_000.0,
    "daily_rate": 0.01,
    "interest": 200_000.0,
    "ending_nav": 99_800_000.0,
}

# Ten USD shares: cost 1,000 USD at 1,300 KRW; sale 1,100 USD at 1,400 KRW.
# Exit fee 0.3%: 1,540,000 * .997 = 1,535,380 KRW.
FX_EXIT = {
    "shares": 10.0,
    "entry_price": 100.0,
    "cost": 1_300_000.0,
    "exit_price": 110.0,
    "exit_fx": 1_400.0,
    "proceeds": 1_535_380.0,
    "pnl": 235_380.0,
    "return_pct": 18.11,
}
