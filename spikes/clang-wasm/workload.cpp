// Representative medium DS&A workload for the Clang-WASM spike.
// Exercises the bits a real exam problem hits on the toolchain: <vector>,
// <algorithm> (sort), <string> parsing, a hash map, and a non-trivial inner loop —
// enough to make the COMPILER and the RUNTIME both do real work, but deterministic.
//
// Problem: read N, then N integers. Print:
//   1) the sorted array (space-separated),
//   2) the count of distinct values,
//   3) the max subarray sum (Kadane) of the ORIGINAL order.
// One query per stdin line: "N a1 a2 ... aN".
//
// Explicit headers (NOT <bits/stdc++.h> — that's a GCC libstdc++ extension; the
// wasm-clang toolchain under test uses libc++, which doesn't ship it).
#include <iostream>
#include <vector>
#include <algorithm>
#include <sstream>
#include <unordered_set>
#include <string>
using namespace std;

int main() {
    string line;
    while (getline(cin, line)) {
        if (line.empty()) continue;
        istringstream ss(line);
        long n; ss >> n;
        vector<long long> a;
        a.reserve(n);
        for (long i = 0; i < n; ++i) { long long x; ss >> x; a.push_back(x); }

        // Kadane on original order.
        long long best = a.empty() ? 0 : a[0], cur = a.empty() ? 0 : a[0];
        for (size_t i = 1; i < a.size(); ++i) {
            cur = max(a[i], cur + a[i]);
            best = max(best, cur);
        }

        unordered_set<long long> distinct(a.begin(), a.end());
        vector<long long> sorted_a = a;
        sort(sorted_a.begin(), sorted_a.end());

        // Output 1: sorted array.
        for (size_t i = 0; i < sorted_a.size(); ++i) {
            if (i) cout << ' ';
            cout << sorted_a[i];
        }
        cout << '\n';
        // Output 2 + 3.
        cout << distinct.size() << '\n';
        cout << best << '\n';
    }
    return 0;
}
