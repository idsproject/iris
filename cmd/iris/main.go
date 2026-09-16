// Command iris is the entry point for the iris HTTP API server. It
// delegates to [app.Run], exiting non-zero if the server fails to start or shut
// down cleanly.
package main

import (
	"context"
	"fmt"
	"os"

	"github.com/idsproject/iris/app"
)

func main() {
	os.Exit(mainErr())
}

func mainErr() int {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()

	if err := run(ctx, os.Args[1:]); err != nil {
		fmt.Fprintln(os.Stderr, err)
		return 1
	}

	return 0
}

// run starts the application with a cancellable background context.
func run(ctx context.Context, args []string) error {
	if len(args) > 0 && args[0] == "migrate" {
		return app.RunMigrations()
	}
	return app.Run(ctx)
}
