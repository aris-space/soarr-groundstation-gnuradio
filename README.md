# gr-sage

GNU Radio out-of-tree module for CCSDS CLTU framing and BCH encoding.

## Development Setup

This repository is configured to build inside the `radioconda` environment on Windows.

### Prerequisites

- Visual Studio 2022 Community or Build Tools
- `radioconda` conda environment
- GNU Radio 3.10.12 in `radioconda`
- Matching Boost development packages in `radioconda`

### Working CMake configuration

The workspace uses these settings in [`.vscode/settings.json`](.vscode/settings.json):

- `cmake.sourceDirectory = C:/ARIS/sage-groundstation-gnuradio/gr-sage`
- `cmake.buildDirectory = C:/ARIS/sage-groundstation-gnuradio/build/gr-sage`
- `cmake.generator = Visual Studio 17 2022`
- `cmake.platform = x64`
- `CMAKE_PREFIX_PATH = C:/Users/yanni/anaconda3/envs/radioconda/Library`
- `Gnuradio_DIR = C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/cmake/gnuradio`
- `MPIR_INCLUDE_DIR = C:/Users/yanni/anaconda3/envs/radioconda/Library/include`
- `MPIR_LIBRARY = C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/mpir.lib`
- `MPIRXX_LIBRARY = C:/Users/yanni/anaconda3/envs/radioconda/Library/lib/mpirxx_static.lib`

### Install the remaining Boost development package

If GNU Radio configure fails on Boost headers, install the matching development package in the same env:

```powershell
conda activate radioconda
conda install -c conda-forge libboost-devel=1.88.0
```

### Configure and build

From the repository root:

```powershell
conda activate radioconda
cmake -S gr-sage -B build/gr-sage -G "Visual Studio 17 2022" -A x64
cmake --build build/gr-sage --config Release
cmake --install build/gr-sage --config Release
```

### Python tests

```powershell
conda activate radioconda
cd gr-sage
pytest .\python\sage\ -q
```

## Notes

- `bchEncoder` implements the CCSDS (63,56) BCH code with complemented parity bits.
- `cltuFramer` prepends the CLTU start sequence and appends the tail sequence.
- Fill bits are handled in the BCH encoder for incomplete 56-bit codeword payloads.
