A/A noise (two halves of the baseline): 0.014 ms; tolerance used: 0.250 ms

| variant | n | median ms | p95 ms | median diff vs foreign | permutation p |
|---|---|---|---|---|---|
| foreign (A's real id) | 400 | 1.739 | 2.288 | 0.0 | None |
| random well-formed, missing | 400 | 1.741 | 2.192 | 0.003 | 0.8741 |
| neighbour of A's id | 400 | 1.73 | 2.251 | -0.009 | 0.6389 |
| malformed (short) | 400 | 1.718 | 2.194 | -0.021 | 0.2885 |
| malformed (space) | 400 | 1.749 | 2.171 | 0.01 | 0.573 |
| malformed (600 chars) | 400 | 1.942 | 2.491 | 0.203 | 0.0007 |
