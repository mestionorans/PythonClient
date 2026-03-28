{
  description = "Vertex Python client — dev shell with Python + pip";

  inputs.nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";

  outputs = { self, nixpkgs }:
    let
      systems = [
        "x86_64-linux"
        "aarch64-linux"
        "aarch64-darwin"
        "x86_64-darwin"
      ];
      forAllSystems = f: nixpkgs.lib.genAttrs systems (system: f nixpkgs.legacyPackages.${system});
    in
    {
      devShells = forAllSystems (pkgs: {
        default = pkgs.mkShell {
          packages = with pkgs; [
            (python3.withPackages (ps: with ps; [
              pip
              setuptools
              wheel
            ]))
          ];

          shellHook = ''
            echo "Vertex client — $(python --version)"
            echo "  python -m venv .venv && source .venv/bin/activate && pip install -e '.[dev]'"
            echo "  (or: pip install -r requirements-dev.txt && pip install -e .)"
          '';
        };
      });
    };
}
