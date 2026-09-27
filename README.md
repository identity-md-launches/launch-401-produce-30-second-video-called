# One block decides

Two finished versions of the same 30-second edit:

| File | Picture | Video | Audio | Duration |
| --- | --- | --- | --- | --- |
| `artifacts/video.mp4` | 1080 × 1080 | H.264, yuv420p | AAC, mono, 48 kHz | 30.000 s |
| `artifacts/video_1920x1080.mp4` | 1920 × 1080 | H.264, yuv420p | AAC, mono, 48 kHz | 30.000 s |

The original soundtrack is synthesized in `scripts/build_video.py`: restrained mechanical pulses at 120 BPM, a final accent at 24.5 s when the last SVG rect arrives, and a clean end at 28 s. There is no voiceover or sampled music. The MP4 files put the `moov` atom first for browser playback and contain no watermark.

## On-chain reads for every on-screen number

The video is pinned to Ethereum mainnet block **26,067,390** (hash `0x29ab89cb09a55f18f512ffc5fd2bb2e93da082131c8b19d551122a91c1a5e00a`). `data/chain_snapshot.json` stores the SVGs, the revealed seeds used for the Gold count, transaction references, and the exact read block. The list below covers every numerical value or identifier shown in the edit, including numbers embedded in labels.

| On-screen value | Chain source and block height |
| --- | --- |
| `#343`, `343` | Token ID in `Minted` at **26,055,815** and `Revealed` at **26,055,971**; name and art checked with `tokenURI(343)` at **26,067,390**. |
| `26,055,815` | `mintBlock` in the `Minted` event for #343, emitted at **26,055,815**. The video formats it without separators. |
| `0x40b24f14c01535d38dedbf15960c57e33b1c2767b9b32de62e4bfb28f7e0d8ad` | Hash returned by `eth_getBlockByNumber` for the block after mint, **26,055,816**; queried during the pinned read at **26,067,390**. |
| `114374586941709767223991903978817664845428934038100222996573977690358389913512` | Seed from `Revealed(343, seed)` at **26,055,971**. It matches `seedOf(343)` at **26,067,390** and a local Keccak-256 recomputation using the next block hash, token ID, event minter, and collection contract. |
| `7` | Count of revealed tokens whose on-chain `PixelArt.attributes(seed)` returned `Skin: Gold`, using reveal events through **26,067,390** and contract calls pinned to that block. |
| `781` | `totalMinted()` at **26,067,390**, cross-checked against 781 `Minted` events through that block. |
| `0x999ce0ce8c5f7661e0c74a568ffe27ceb9177bdb` | SwarmPepe contract address in the creation receipt at **26,054,542**, used for the calls at **26,067,390**. It is the contract address, not a person's wallet. |
| `256` in `keccak256` and `reveal(uint256[])` | The 256-bit Keccak function and Solidity `uint256[]` parameter in the SwarmPepe contract deployed at **26,054,542**. The verified contract source and ABI were inspected for this edit. These are type/function names, not changing collection counts. |

The on-screen `Skin: Gold` is the attribute in `tokenURI(343)` at **26,067,390**, corroborated by `PixelArt.attributes(seed)` at the same block. The unfinished placeholder came from `PixelArt.placeholderSVG()`. The final image came from the SVG inside `tokenURI(343)` and was byte-for-byte equal to `PixelArt.renderSVG(seed)`. Its 21 rects are drawn in their source order; the earring pixel is last. Only integer scaling is used for the artwork.

## Build and verification

`python3 scripts/build_video.py` refreshes the chain snapshot, emits one PNG for each frame into `test/scratch/render/`, synthesizes the track, and assembles both MP4s with ffmpeg. `python3 scripts/build_video.py --offline` rebuilds the exact pinned edit without network access. It needs Python 3's standard library, ffmpeg, and ffprobe; no Python package or external asset is required.

The script validates the mint and reveal events, recomputes the seed, checks `totalMinted()`, queries `PixelArt.attributes()` for every revealed token, and compares the decoded `tokenURI(343)` SVG to `renderSVG(seed)`. Final files were probed for 30.000 s duration, both requested dimensions, H.264 video, and AAC audio. Representative output frames were visually inspected.

**Limitation:** the Gold count and minted total describe block 26,067,390. Later mints or reveals will change the live counts; rerun without `--offline` to refresh them. The final two seconds are deliberately black and silent.
