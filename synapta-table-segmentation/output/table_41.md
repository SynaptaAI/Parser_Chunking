| A B |  | Cc | D | E |  |
| --- | --- | --- | --- | --- | --- |
| Implicit |  |  | Squared | Gross Return | | Wealth |
| 41 Year Probability |  | HPR (decimal) | Deviation | =1+HPR | Index* |
| 2 1 0.20 |  | -0.1189 | 0.0196 | 0.8811 | 0.8811 |
| 3 2 0.20 |  | -0.2210 | 0.0586 |  | 0.6864 |
| 4 3 0.20 |  | 0.2869 | 0.0707 | 1.2869 | | 0.8833 |
| 5 4 0.20 |  | 0.1088 | 0.0077 | 1.1088 | | 0.9794 |
| 6 5 0.20 |  | 0.0491 | 0.0008 | 1.0491 | 1.0275 |
| 7 | Arithmetic average |= AVERAGE(C2:C6) |  | 0.0210 |  |  |  |
| 8 | Expected HPR SUMPRODUCT(B2:B6, | C2:C6) | 0.0210 |  |  |  |
| 9 | Variance SUMPRODUCT(B2:B6, | D2:D6) |  | 0.0315 |  |  |
| 10 | Standard deviation SQRT(D9) |  |  | 0.1774 |  |  |
| |11 | Standard deviation STDEV.P(C2:C6) |  |  | 0.1774 |  |  |
| 42 | Std dev (df = 4) SQRT(D9*5/4) |  |  | 0.1983 |  |  |
| 43 | Std dev (df = 4) STDEV.S(C2:C6) |  |  | 0.1983 |  |  |
| 14 | Geometric avg return | |  |  |  |  | 0.0054 |
| 15 |  |  |  |  |  |
| 16 * The wealth index is the cumulative value | of $1 invested at | the beginning of the | sample period. |  |  |
