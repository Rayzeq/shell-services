{ pkgs }:
args @ { services ? { }, buildInputs ? [ ], shellHook ? "", postShellHook ? "", ... }:
let
  lib = pkgs.lib;
  inherit (lib) mkOption;

  typecheckedServices = lib.evalModules {
    modules = [
      {
        options.services = mkOption {
          type = with lib.types; attrsOf (either
            (listOf str)
            (submodule {
              options = {
                working-directory = mkOption {
                  type = nullOr str;
                  description = "Directory in which the command will be launched. If not absolute, this path is relative to the directory containing the .direnv file.";
                  default = null;
                };
                env = mkOption {
                  type = attrsOf str;
                  description = "Environment variables to forward to the service";
                  default = { };
                };
                start = mkOption {
                  type = listOf str;
                  description = "Command to start the service";
                };
                check = mkOption {
                  type = nullOr str;
                  description = "Command to check whether the service should be started / has been stopped";
                  default = null;
                };
              };
            })
          );
        };
        config.services = services;
      }
    ];
  };
  canonicalizedServices = lib.mapAttrs
    (name: service:
      if lib.isList service then
        { start = service; }
      else
        service
    )
    typecheckedServices.config.services;

  command = pkgs.writeShellScriptBin "services" ''
    export SERVICES='${lib.toJSON canonicalizedServices}'
    ${pkgs.python3}/bin/python ${./services.py} $@
  '';
in
pkgs.mkShell
  {
    buildInputs = buildInputs ++ [ command ];
    shellHook = shellHook + ''
      services --auto --pid $$ start
    '' + postShellHook;
  }
  // (removeAttrs args [ "services" "buildInputs" "shellHook" ])
