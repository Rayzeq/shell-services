# shell-services

A utility to automatically manage background services (like databases, caches, or background workers) for your `shell.nix`.

Services automatically start when you enter your project and shut down in the background when you close your last terminal window or leave the directory.

## Features

- **Automatic Lifecycle**: services start when you enter your development environment and stop when you leave. `direnv` is fully supported.

- **Multi-Terminal Support**: safely handles having multiple terminal tabs or windows open in the same project without starting duplicate services, and wait for all of them to be closed to stop services.

- **Project-Isolated**: services are securely scoped to your specific project folder, preventing conflicts if you have multiple projects using the same service names.

- **Background Execution**: services run cleanly in the background without cluttering your terminal output.


## Setup

To use the service manager, import the Nix environment and define your required services.

```nix
{ pkgs ? import <nixpkgs> { } }:
let
  mkShell = import (fetchGit "https://github.com/Rayzeq/shell-services.git") { inherit pkgs; };
in
mkShell {
  # Your normal development dependencies
  buildInputs = with pkgs; [ nodejs postgresql ];

  shellHook = ''
    # You can setup things for your services here,
    # as they will be started at the end of the shellHook

    if [[ ! -d ./.pgdata ]]; then
      mkdir -p ./.pgdata
      pg_ctl -D ./.pgdata init
      mkdir -p ./.pgdata/run
    fi
  '';
  
  # Define the background services for this project
  services = {
    postgres = {
      start = [ "postgres" "-D" "./.pgdata" "-k" "\${PWD}/.pgdata/run" ];
      # Optional: command to check whether the service is running or not
      check = "pg_ctl status -D ./.pgdata > /dev/null";
      # Optional: add environment variables
      # env = {
      #   TEST = "something here";
      # };
    };

    # Shorthand for { start = [...]; }
    redis = [ "${pkgs.redis}/bin/redis-server" ]; 
  };
}
```

## Commands

Once you are inside the shell, your services will automatically boot up. You can also manually control them using the generated `services` command:

- `services start [name]`

    Starts all project services. If a specific `name` is provided, it only starts that service.

- `services stop [name]`

    Stops all project services. If a specific `name` is provided, it only stops that service.

- `services status [name]`

    Displays the current status (e.g., running, dead, failed) for all project services, or prints detailed status information if a specific service is queried.