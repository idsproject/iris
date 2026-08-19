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
	if err := run(); err != nil {
		fmt.Fprintln(os.Stderr, err.Error())
		os.Exit(1)
	}
}

// run starts the application with a cancellable background context.
func run() error {
	ctx, cancel := context.WithCancel(context.Background())
	defer cancel()
	return app.Run(ctx)
}
