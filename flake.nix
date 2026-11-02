{
  description = "BOM Highlighter Flake";

  inputs = {
    nixpkgs.url = "github:nixos/nixpkgs/nixos-unstable";
  };

  outputs = { self, nixpkgs }:
  let
    supportedSystems = [
      "aarch64-darwin"
      "aarch64-linux"
      "x86_64-darwin"
      "x86_64-linux"
    ];
    forAllSystems = nixpkgs.lib.genAttrs supportedSystems;
    pkgsFor = forAllSystems (system: import nixpkgs { inherit system; });
  in {
    devShells = forAllSystems (system:
    let
      pkgs = pkgsFor.${system};
    in {
      default = pkgs.mkShell {
        buildInputs = with pkgs; [
          python3
          typst
        ];

        shellHook = ''
          echo "Using BOM Highlighter devShell!"
          python3 --version
          echo ""
        '';
      };
    });

    packages = forAllSystems (system:
    let
      pkgs = pkgsFor.${system};
    in rec {
      hbom = pkgs.writers.writePython3Bin "hbom" {
        flakeIgnore = [ "E501" ];
      } (builtins.readFile ./hbom.py);

      default = hbom;
    });
  };
}
