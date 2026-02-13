| A | B | c |  | D |  | le |  |
| --- | --- | --- | --- | --- | --- | --- | --- |
| 4 2.25% coupon bond, |  |  | 3% | coupon bond, |  | 8% coupon | bond, |
| 2 maturing Aug 2049 | Formula | in column | B maturing | November | 2044| | 30-year maturity |  |
| 3 |  |  |  |  |  |  |  |
| 4 |Settlement date 8/15/2021 | | =DATE (2021, | 8, 15) | 8/15/2021 |  |  | 1/1/2000 |  |
| 5 |Maturity date 8/15/2049 | | =DATE (2049, | 8, 15) | 11/15/2044 |  |  | 1/1/2030 |  |
| 6 |Annual coupon rate | 0.0225 |  |  | 0.03 |  |  | 0.08 |
| 7 to maturity 0.01938 |  |  |  | 0.01904 |  |  | 0.1 |
| 8 |Redemption value (% of face value) | 100 |  |  | 100 |  |  | 100 |
| 9 |Coupon payments per year | 2 |  |  | 2 |  |  | 2 |
| 10 |  |  |  |  |  |  |  |
| 11 |  |  |  |  |  |  |  |
| 412 |Flat price (% of par) 106.7176 | | =PRICE(B4,B5,B6,B7,B8,B9) |  |  |  |  | 81.0707 |  |
| 413 | Days since last coupon | O | =COUPDAYBS(B4,B5,2,1) |  |  | 92 |  |  | oO |
| 44 in coupon period | 184 | =COUPDAYS(B4,B5,2,1) |  |  | 184 |  |  | 182 |
| 45 | Accrued interest | 0 | |  |  | 0.750 |  |  |  |
| 416 | Invoice price 106.7176 | | =B12+B15 |  | 121.2603 |  |  | 81.0707 |  |
