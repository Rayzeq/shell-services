{ pkgs }:
args @ { services ? { }, buildInputs ? [ ], shellHook ? "", ... }:
let
  lib = pkgs.lib;
  servicesCanonicalized = lib.mapAttrs
    (name: service:
      if lib.isList service then
        { start = service; }
      else
        service
    )
    services;
  command = pkgs.writeShellScriptBin "services" ''
    export SERVICES='${lib.toJSON servicesCanonicalized}'
    ${pkgs.python3}/bin/python ${./services.py} $@
  '';
in
pkgs.mkShell
  {
    buildInputs = buildInputs ++ [ command ];
    shellHook = shellHook + ''
      services --auto --pid $$ start
    '';
  }
  // (removeAttrs args [ "services" "buildInputs" "shellHook" ])
