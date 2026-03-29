{
  description = "Vertex Python client — dev shell with Python + pip + node (React UI)";

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
            # Needed to build pydantic-core (NiceGUI dependency) from source
            rustc
            cargo
            # Frontend tooling (React)
            nodejs
            (python3.withPackages (ps: with ps; [
              pip
              setuptools
              wheel
            ]))
          ];

          shellHook = ''
            PYBIN="$(command -v python3 || true)"
            if [ -z "$PYBIN" ]; then
              PYBIN="$(command -v python || true)"
            fi

            if [ -z "$PYBIN" ]; then
              echo "error: no python found in nix dev shell"
              exit 1
            fi

            # Ensure the local venv exists and has web deps installed.
            if [ ! -x .venv/bin/python ] || ! .venv/bin/python -c "import fastapi" >/dev/null 2>&1; then
              echo "vertex: creating/recreating .venv (missing backend deps)."
              rm -rf .venv
              "$PYBIN" -m venv .venv
              . .venv/bin/activate
              python -m pip install -U pip setuptools wheel
              pip install -r requirements.txt
              pip install -e '.[dev]'
            fi

            echo "Vertex client — $(python --version)"
            echo "  Node: $(node --version) / npm $(npm --version)"
          '';
        };
      });
    };
}
