# hbom

A PCA BOM table highlighter tool

version 0.2

## Usage

```
usage: hbom [-h] [-x [PICK]] [-o [OUTPUT]] [-p [PRE_HEADER]] [-y] [-v] BOM

A PCA BOM table highlighter tool

positional arguments:
  BOM                   BOM file to be highlighted

options:
  -h, --help            show this help message and exit
  -x, --pick [PICK]     pick and place file
  -o, --output [OUTPUT]
                        output file to write to
  -p, --pre-header [PRE_HEADER]
                        rows before the header (default: 0)
  -y, --yes             skip unrequired user prompts
  -v, --verbose         show all status messages
```

## Building with Nix commands

On systems with Nix installed and Nix commands enabled.

To build a binary:

`nix build github:riley-indtec/hbom`

To run without installing:

`nix run github:riley-indtec/hbom`

To enter a temporary shell with `hbom` installed:

`nix shell github:riley-indtec/hbom`

To install to a Nix profile:

`nix profile add github:riley-indtec/hbom`
