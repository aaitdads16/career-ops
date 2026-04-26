# Changelog

## [1.6.0](https://github.com/aaitdads16/career-ops/compare/v1.5.0...v1.6.0) (2026-04-26)


### Features

* 8 new features + bug fixes ([eeacc43](https://github.com/aaitdads16/career-ops/commit/eeacc43610a806c2c4c1f02736cc8becb495187a))
* add 5 new features — Applied button, outreach, skills gap, analytics, ATS score ([358fd15](https://github.com/aaitdads16/career-ops/commit/358fd15865d02ea384cebb20f55cedcacf6a1049))
* add internship automation layer on top of career-ops ([28195a1](https://github.com/aaitdads16/career-ops/commit/28195a10367fd1e38000053f3df0f53ba204a565))
* ATS resume redesign, Nemotron project, PDF fixes ([a428b88](https://github.com/aaitdads16/career-ops/commit/a428b885ab5fd9c141a44920124ea47928c06c82))
* dashboard, Gmail integration, auto Telegram bot, real balance tracking ([4743cb7](https://github.com/aaitdads16/career-ops/commit/4743cb7b74094af1045c7849eb3357c544e680b4))
* direct dashboard status updates via GitHub API ([2288b83](https://github.com/aaitdads16/career-ops/commit/2288b83920731d419bd930dde116a023e89a93d3))
* Gmail ATS detection, 40+ locations, workflow dashboard panel, PDF downloads, /gmailsync ([74843c3](https://github.com/aaitdads16/career-ops/commit/74843c3c70a99064e734947ce0aed891c2bf099f))
* JD archiving + /regenerate + /pending bulk-apply command ([dc93d7a](https://github.com/aaitdads16/career-ops/commit/dc93d7a00a44bf44fbcb76b4fb0651d79b5e8540))


### Bug Fixes

* ATS-optimized resume prompt + tighten relevance filter ([d3ca141](https://github.com/aaitdads16/career-ops/commit/d3ca14148118214b4728148fe40d6203724221bb))
* credit exhaustion breaks doc loop, within-run dedup, merge-ours on push, remove Israel ([9518079](https://github.com/aaitdads16/career-ops/commit/95180797b68f19634f97f02f00e032f5bce3bb83))
* detect Apify quota exhaustion early, abort + alert instead of 200 silent failures ([5632ccf](https://github.com/aaitdads16/career-ops/commit/5632ccfbe0b8ee6a74ab81161a4a15fa59d673f3))
* eliminate duplicate offers in Telegram reports ([c939979](https://github.com/aaitdads16/career-ops/commit/c939979dcde1e7410371d26852b3f6631e4a15de))
* LinkedIn scraping — remove f_JT=I filter, add splitByLocation tier-1, count=100 ([8bb403d](https://github.com/aaitdads16/career-ops/commit/8bb403df84a81620ba84a41953e9b01b36ac82b3))
* LinkedIn-only dominance + add Google Jobs source ([cf9d307](https://github.com/aaitdads16/career-ops/commit/cf9d30779d7250f4aaeaa59ebe8ad1a7f9146c31))
* rebuild templates + 2-step content generation ([f9293e4](https://github.com/aaitdads16/career-ops/commit/f9293e48284a8be2ad964b6fc8eaabd97d4db592))
* remove npm cache (package-lock.json gitignored), upgrade Node to 22 ([188a204](https://github.com/aaitdads16/career-ops/commit/188a204ae756b2b8a003c7f8c138988bb579f736))
* remove npm cache from workflow (no package-lock.json in repo) ([9fa2bd6](https://github.com/aaitdads16/career-ops/commit/9fa2bd6f86ca608994706e527018b924669f37ee))
* telegram applied button, live dashboard overlay, branch-based Pages deploy ([fd33693](https://github.com/aaitdads16/career-ops/commit/fd336933a65ed0cadeddd477a87b383c8e248f0d))

## [1.5.0](https://github.com/santifer/career-ops/compare/v1.4.0...v1.5.0) (2026-04-14)


### Features

* add --min-score flag to batch runner ([#249](https://github.com/santifer/career-ops/issues/249)) ([cb0c7f7](https://github.com/santifer/career-ops/commit/cb0c7f7d7d3b9f3f1c3dc75ccac0a08d2737c01e))
* add {{PHONE}} placeholder to CV template ([#287](https://github.com/santifer/career-ops/issues/287)) ([e71595f](https://github.com/santifer/career-ops/commit/e71595f8ba134971ecf1cc3c3420d9caf21eed43))
* **dashboard:** add manual refresh shortcut ([#246](https://github.com/santifer/career-ops/issues/246)) ([4b5093a](https://github.com/santifer/career-ops/commit/4b5093a8ef1733c449ec0821f722f996625fcb84))


### Bug Fixes

* add stopword filtering and overlap ratio to roleMatch ([#248](https://github.com/santifer/career-ops/issues/248)) ([4da772d](https://github.com/santifer/career-ops/commit/4da772d3a4996bc9ecbe2d384d1e9d2ed75b9819))
* **dashboard:** show dates in pipeline list ([#298](https://github.com/santifer/career-ops/issues/298)) ([e5e2a6c](https://github.com/santifer/career-ops/commit/e5e2a6cffe9a5b9f3cec862df25410d02ecc9aa4))
* ensure data/ and output/ dirs exist before writing in scripts ([#261](https://github.com/santifer/career-ops/issues/261)) ([4b834f6](https://github.com/santifer/career-ops/commit/4b834f6f7f8f1b647a6bf76e43b017dcbe9cd52f))
* remove wellfound, lever and remotefront from portals.example.yml ([#286](https://github.com/santifer/career-ops/issues/286)) ([ecd013c](https://github.com/santifer/career-ops/commit/ecd013cc6f59e3a1a8ef77d34e7abc15e8075ed3))

## [1.4.0](https://github.com/santifer/career-ops/compare/v1.3.0...v1.4.0) (2026-04-13)


### Features

* add GitHub Actions CI + auto-labeler + welcome bot + /run skill ([2ddf22a](https://github.com/santifer/career-ops/commit/2ddf22a6a2731b38bcaed5786c4855c4ab9fe722))
* **dashboard:** add Catppuccin Latte light theme with auto-detection ([ff686c8](https://github.com/santifer/career-ops/commit/ff686c8af97a7bf93565fe8eeac677f998cc9ece))
* **dashboard:** add progress analytics screen ([623c837](https://github.com/santifer/career-ops/commit/623c837bf3155fd5b7413554240071d40585dd7e))
* **dashboard:** add vim motions to pipeline screen ([#262](https://github.com/santifer/career-ops/issues/262)) ([d149e54](https://github.com/santifer/career-ops/commit/d149e541402db0c88161a71c73899cd1836a1b2d))
* **dashboard:** aligned tables and markdown syntax rendering in viewer ([dbd1d3f](https://github.com/santifer/career-ops/commit/dbd1d3f7177358d0384d6e661d1b0dfc1f60bd4e))


### Bug Fixes

* **ci:** use pull_request_target for labeler on fork PRs ([#260](https://github.com/santifer/career-ops/issues/260)) ([2ecf572](https://github.com/santifer/career-ops/commit/2ecf57206c2eb6e35e2a843d6b8365f7a04c53d6))
* correct _shared.md → _profile.md reference in CUSTOMIZATION.md (closes [#137](https://github.com/santifer/career-ops/issues/137)) ([a91e264](https://github.com/santifer/career-ops/commit/a91e264b6ea047a76d8c033aa564fe01b8f9c1d9))
* replace grep -P with POSIX-compatible grep in batch-runner.sh ([637b39e](https://github.com/santifer/career-ops/commit/637b39e383d1174c8287f42e9534e9e3cdfabb19))
* test-all.mjs scans only git-tracked files, avoids false positives ([47c9f98](https://github.com/santifer/career-ops/commit/47c9f984d8ddc70974f15c99b081667b73f1bb9a))
* use execFileSync to prevent shell injection in test-all.mjs ([c99d5a6](https://github.com/santifer/career-ops/commit/c99d5a6526f923b56c3790b79b0349f402fa00e2))
