# Run state codec measurements

Generated from [standard.json](results/standard.json) and [large-state.json](results/large-state.json). See [methodology and limitations](README.md) before interpreting these numbers.

Base: `3d8d5ec20f3571243315f1bf6c75365aae820039`. CPU: 12th Gen Intel(R) Core(TM) i5-12450H. Python: 3.13.7. Filesystem: zfs. Dates: 2026-09-07T17:41:19.742527+00:00 to 2026-09-07T18:10:58.404048+00:00.

## Stored/transfer body sizes

Exact canonical/encoded byte counts. Percent is encoded / canonical (lower is better), not percent saved. Bytes per object PUT or GET exclude transport and backend framing. The actual local file adds 65,548 bytes in every row.

| Case                      | Raw bytes  | zstd-1             | zstd-3             | lz4-0               |
| ------------------------- | ---------- | ------------------ | ------------------ | ------------------- |
| repository-history/small  | 20,343     | 7,242 (35.6%)      | 6,909 (34.0%)      | 10,018 (49.2%)      |
| repository-history/medium | 296,609    | 76,326 (25.7%)     | 70,516 (23.8%)     | 109,400 (36.9%)     |
| repository-history/large  | 4,719,038  | 1,146,269 (24.3%)  | 1,009,963 (21.4%)  | 1,673,624 (35.5%)   |
| tool-results/small        | 22,650     | 4,165 (18.4%)      | 4,183 (18.5%)      | 6,358 (28.1%)       |
| tool-results/medium       | 291,971    | 36,467 (12.5%)     | 37,850 (13.0%)     | 61,446 (21.0%)      |
| tool-results/large        | 4,588,112  | 548,965 (12.0%)    | 578,781 (12.6%)    | 947,591 (20.7%)     |
| entropy/small             | 20,346     | 13,849 (68.1%)     | 13,792 (67.8%)     | 18,174 (89.3%)      |
| entropy/medium            | 295,738    | 199,910 (67.6%)    | 199,779 (67.6%)    | 267,310 (90.4%)     |
| entropy/large             | 4,693,369  | 3,168,759 (67.5%)  | 3,169,602 (67.5%)  | 4,239,975 (90.3%)   |
| image-jpeg/small          | 38,550     | 26,960 (69.9%)     | 26,941 (69.9%)     | 35,736 (92.7%)      |
| image-jpeg/medium         | 533,866    | 398,697 (74.7%)    | 398,335 (74.6%)    | 532,897 (99.8%)     |
| image-jpeg/large          | 6,351,510  | 4,759,329 (74.9%)  | 4,755,086 (74.9%)  | 6,351,338 (100.0%)  |
| codeact-values/small      | 17,442     | 7,353 (42.2%)      | 7,128 (40.9%)      | 11,344 (65.0%)      |
| codeact-values/medium     | 241,768    | 91,117 (37.7%)     | 100,673 (41.6%)    | 153,429 (63.5%)     |
| codeact-values/large      | 3,831,922  | 1,461,913 (38.2%)  | 1,509,888 (39.4%)  | 2,421,948 (63.2%)   |
| image-jpeg/xlarge         | 45,508,361 | 34,099,743 (74.9%) | 34,081,016 (74.9%) | 45,507,782 (100.0%) |
| codeact-values/xlarge     | 30,637,679 | 11,680,380 (38.1%) | 12,101,875 (39.5%) | 19,358,790 (63.2%)  |

## Canonical serialization and strict recovery

Median wall ms, independently timed without object I/O or compression. Strict recovery includes parse, canonical re-encoding/comparison, and model validation, not just JSON parsing.

| Case                      | Messages | Canonical encode ms | Strict decode ms |
| ------------------------- | -------- | ------------------- | ---------------- |
| repository-history/small  | 4        | 0.348               | 0.464            |
| repository-history/medium | 64       | 4.440               | 5.808            |
| repository-history/large  | 1024     | 72.101              | 86.898           |
| tool-results/small        | 6        | 2.855               | 2.675            |
| tool-results/medium       | 84       | 36.052              | 35.475           |
| tool-results/large        | 1326     | 590.709             | 553.981          |
| entropy/small             | 4        | 0.435               | 0.574            |
| entropy/medium            | 64       | 5.279               | 5.902            |
| entropy/large             | 1022     | 86.727              | 88.824           |
| image-jpeg/small          | 4        | 3.571               | 4.321            |
| image-jpeg/medium         | 4        | 29.141              | 37.904           |
| image-jpeg/large          | 12       | 275.463             | 283.246          |
| codeact-values/small      | 1        | 3.158               | 3.341            |
| codeact-values/medium     | 1        | 49.940              | 46.835           |
| codeact-values/large      | 1        | 750.197             | 710.080          |
| image-jpeg/xlarge         | 86       | 1605.038            | 1676.506         |
| codeact-values/xlarge     | 1        | 6108.437            | 5710.631         |

## Codec-only cost for large states

Fresh codec context per operation; checksums and frame content sizes enabled. Median wall / process CPU ms. Raw is an identity baseline, not another JSON serialization. No multi-threaded zstd compression.

| Case                     | Codec  | Compress wall / CPU ms | Decompress wall / CPU ms |
| ------------------------ | ------ | ---------------------- | ------------------------ |
| repository-history/large | zstd-1 | 12.499 / 12.478        | 3.395 / 3.393            |
| repository-history/large | zstd-3 | 18.349 / 18.341        | 3.759 / 3.754            |
| repository-history/large | lz4-0  | 11.754 / 11.747        | 4.132 / 4.125            |
| tool-results/large       | zstd-1 | 6.263 / 6.258          | 2.503 / 2.499            |
| tool-results/large       | zstd-3 | 8.412 / 8.406          | 2.138 / 2.135            |
| tool-results/large       | lz4-0  | 5.826 / 5.820          | 2.387 / 2.384            |
| entropy/large            | zstd-1 | 9.633 / 9.579          | 6.851 / 6.841            |
| entropy/large            | zstd-3 | 12.843 / 12.833        | 6.751 / 6.743            |
| entropy/large            | lz4-0  | 5.744 / 5.697          | 3.137 / 3.131            |
| image-jpeg/large         | zstd-1 | 7.865 / 7.858          | 7.788 / 7.782            |
| image-jpeg/large         | zstd-3 | 9.760 / 9.753          | 8.571 / 8.553            |
| image-jpeg/large         | lz4-0  | 5.317 / 5.309          | 3.439 / 3.434            |
| codeact-values/large     | zstd-1 | 14.072 / 14.065        | 4.079 / 4.073            |
| codeact-values/large     | zstd-3 | 31.086 / 31.078        | 4.784 / 4.778            |
| codeact-values/large     | lz4-0  | 11.960 / 11.936        | 3.153 / 3.146            |
| image-jpeg/xlarge        | zstd-1 | 63.206 / 63.094        | 66.610 / 66.580          |
| image-jpeg/xlarge        | zstd-3 | 69.856 / 69.832        | 65.421 / 65.232          |
| image-jpeg/xlarge        | lz4-0  | 64.368 / 64.361        | 50.431 / 50.425          |
| codeact-values/xlarge    | zstd-1 | 105.151 / 105.137      | 32.042 / 32.037          |
| codeact-values/xlarge    | zstd-3 | 230.606 / 230.588      | 37.647 / 37.641          |
| codeact-values/xlarge    | lz4-0  | 82.670 / 82.523        | 19.359 / 19.355          |

## Local storage-boundary latency for large states

Measured boundary harness, **not full Service checkpoint/recovery**. Median / p95 ms. C1 = one worker; C8 = eight workers on independent keys. Only added compression is offloaded; existing canonical JSON work stays synchronous. Lag is maximum observed excess delay of a 5 ms ticker in the C8 trial. Standard: 7 samples at C1, 56 at C8; xlarge: 3 at C1, C8 not measured (resource-bounded stress run). Small-sample p95 is descriptive only (C1 p95 is the maximum).

| Case                     | Codec  | C1 write        | C1 read         | C8 write         | C8 read         | C8 max lag   |
| ------------------------ | ------ | --------------- | --------------- | ---------------- | --------------- | ------------ |
| repository-history/large | raw    | 79.1 / 82.9     | 93.9 / 98.9     | 495.6 / 831.0    | 462.2 / 598.4   | 876.8        |
| repository-history/large | zstd-1 | 91.4 / 98.9     | 97.1 / 102.7    | 485.5 / 929.9    | 342.9 / 588.1   | 897.6        |
| repository-history/large | zstd-3 | 105.9 / 120.8   | 102.8 / 111.1   | 586.2 / 917.2    | 304.1 / 576.8   | 635.7        |
| repository-history/large | lz4-0  | 91.6 / 99.2     | 99.8 / 364.4    | 514.2 / 992.7    | 392.1 / 546.8   | 892.0        |
| tool-results/large       | raw    | 604.1 / 878.2   | 574.0 / 605.3   | 3189.2 / 5262.5  | 2444.5 / 3786.2 | 5245.2       |
| tool-results/large       | zstd-1 | 601.9 / 640.8   | 594.6 / 854.9   | 4456.5 / 7332.1  | 2315.1 / 3554.3 | 6021.5       |
| tool-results/large       | zstd-3 | 581.6 / 595.1   | 559.4 / 828.0   | 3782.4 / 7315.5  | 1959.6 / 3218.7 | 5504.1       |
| tool-results/large       | lz4-0  | 641.0 / 651.4   | 611.0 / 877.9   | 3903.0 / 8431.1  | 2478.9 / 4016.0 | 8604.4       |
| entropy/large            | raw    | 95.9 / 140.5    | 107.2 / 153.5   | 544.0 / 1122.2   | 554.9 / 774.9   | 943.7        |
| entropy/large            | zstd-1 | 98.6 / 125.6    | 108.7 / 121.6   | 473.4 / 826.2    | 473.9 / 898.1   | 669.3        |
| entropy/large            | zstd-3 | 99.0 / 112.7    | 97.9 / 403.2    | 547.3 / 931.6    | 463.7 / 1255.3  | 873.1        |
| entropy/large            | lz4-0  | 87.9 / 92.2     | 91.8 / 103.4    | 437.0 / 821.7    | 458.2 / 882.5   | 691.7        |
| image-jpeg/large         | raw    | 255.1 / 267.7   | 256.2 / 276.1   | 1297.9 / 2282.2  | 1289.9 / 2069.9 | 2512.1       |
| image-jpeg/large         | zstd-1 | 281.1 / 399.0   | 288.8 / 313.2   | 1280.4 / 2218.3  | 1240.3 / 2100.1 | 2236.0       |
| image-jpeg/large         | zstd-3 | 282.5 / 310.8   | 275.6 / 306.5   | 1301.9 / 2427.6  | 1244.4 / 1984.6 | 2249.1       |
| image-jpeg/large         | lz4-0  | 261.4 / 265.1   | 265.6 / 278.6   | 1351.5 / 2319.5  | 1417.2 / 2344.8 | 2350.9       |
| codeact-values/large     | raw    | 958.9 / 1477.7  | 849.2 / 1182.1  | 4142.9 / 7502.1  | 3119.4 / 4661.0 | 7485.8       |
| codeact-values/large     | zstd-1 | 810.9 / 1114.1  | 718.3 / 975.2   | 4879.5 / 8235.1  | 2953.3 / 4467.6 | 7048.6       |
| codeact-values/large     | zstd-3 | 818.1 / 1114.2  | 731.5 / 1055.3  | 5668.3 / 11880.8 | 2972.9 / 5037.1 | 10724.6      |
| codeact-values/large     | lz4-0  | 739.4 / 998.6   | 659.4 / 941.3   | 3999.7 / 6980.9  | 3028.2 / 4017.2 | 6603.2       |
| image-jpeg/xlarge        | raw    | 1716.8 / 1763.7 | 1850.6 / 1884.1 | not measured     | not measured    | not measured |
| image-jpeg/xlarge        | zstd-1 | 1725.2 / 1749.7 | 1865.0 / 1874.3 | not measured     | not measured    | not measured |
| image-jpeg/xlarge        | zstd-3 | 1811.4 / 1849.1 | 1824.6 / 1902.5 | not measured     | not measured    | not measured |
| image-jpeg/xlarge        | lz4-0  | 1760.7 / 1822.2 | 1836.5 / 1871.4 | not measured     | not measured    | not measured |
| codeact-values/xlarge    | raw    | 6101.1 / 6202.8 | 5524.3 / 5589.4 | not measured     | not measured    | not measured |
| codeact-values/xlarge    | zstd-1 | 6187.5 / 6224.1 | 5666.6 / 5764.5 | not measured     | not measured    | not measured |
| codeact-values/xlarge    | zstd-3 | 6719.7 / 7762.0 | 6229.8 / 6784.5 | not measured     | not measured    | not measured |
| codeact-values/xlarge    | lz4-0  | 6248.4 / 6282.7 | 5747.5 / 5747.6 | not measured     | not measured    | not measured |

## Codec process memory for large states

Median of 3 fresh Linux processes per cell, MiB. Each cell is **total VmHWM / increase over pre-operation VmHWM**. Includes native allocations and retained output; baseline already includes the operation's input. Decode starts from an encoded file, not from compressing in that process. Does not measure whole-Service or concurrent peak memory. HWM increments can undercount allocations that fit below a previous import/input-load peak; this is not an allocator-exact workspace measurement. Full baseline and retained-RSS samples are in JSON.

| Case                     | Codec  | Compress total / extra MiB | Decompress total / extra MiB |
| ------------------------ | ------ | -------------------------- | ---------------------------- |
| repository-history/large | raw    | 17.3 / 0.0                 | 17.5 / 0.0                   |
| repository-history/large | zstd-1 | 18.8 / 1.4                 | 18.6 / 4.5                   |
| repository-history/large | zstd-3 | 19.4 / 2.0                 | 18.3 / 4.5                   |
| repository-history/large | lz4-0  | 20.7 / 3.2                 | 23.4 / 9.0                   |
| tool-results/large       | raw    | 17.3 / 0.0                 | 17.2 / 0.0                   |
| tool-results/large       | zstd-1 | 18.0 / 0.8                 | 17.8 / 4.4                   |
| tool-results/large       | zstd-3 | 18.8 / 1.5                 | 17.9 / 4.4                   |
| tool-results/large       | lz4-0  | 19.0 / 1.8                 | 22.5 / 8.8                   |
| entropy/large            | raw    | 17.5 / 0.0                 | 17.3 / 0.0                   |
| entropy/large            | zstd-1 | 20.6 / 3.4                 | 20.4 / 4.5                   |
| entropy/large            | zstd-3 | 21.6 / 4.1                 | 20.5 / 4.5                   |
| entropy/large            | lz4-0  | 25.5 / 8.1                 | 25.8 / 8.9                   |
| image-jpeg/large         | raw    | 19.0 / 0.0                 | 19.0 / 0.0                   |
| image-jpeg/large         | zstd-1 | 23.6 / 4.9                 | 23.6 / 6.3                   |
| image-jpeg/large         | zstd-3 | 24.6 / 5.5                 | 23.5 / 6.1                   |
| image-jpeg/large         | lz4-0  | 31.1 / 12.1                | 31.2 / 12.1                  |
| codeact-values/large     | raw    | 16.3 / 0.0                 | 16.7 / 0.0                   |
| codeact-values/large     | zstd-1 | 18.4 / 1.8                 | 18.0 / 3.8                   |
| codeact-values/large     | zstd-3 | 19.2 / 2.5                 | 18.0 / 3.8                   |
| codeact-values/large     | lz4-0  | 21.1 / 4.6                 | 22.6 / 7.4                   |
| image-jpeg/xlarge        | raw    | 56.3 / 0.0                 | 56.3 / 0.0                   |
| image-jpeg/xlarge        | zstd-1 | 89.2 / 32.9                | 88.9 / 43.5                  |
| image-jpeg/xlarge        | zstd-3 | 89.7 / 33.5                | 88.6 / 43.4                  |
| image-jpeg/xlarge        | lz4-0  | 143.1 / 86.8               | 143.2 / 86.9                 |
| codeact-values/xlarge    | raw    | 42.1 / 0.0                 | 42.1 / 0.0                   |
| codeact-values/xlarge    | zstd-1 | 53.6 / 11.5                | 53.3 / 29.2                  |
| codeact-values/xlarge    | zstd-3 | 54.7 / 12.6                | 53.6 / 29.1                  |
| codeact-values/xlarge    | lz4-0  | 78.9 / 36.9                | 89.8 / 58.5                  |
