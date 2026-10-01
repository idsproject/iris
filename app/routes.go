package app

import (
	"fmt"
	"net/http"

	"github.com/idsproject/iris/api"
	"github.com/idsproject/iris/handler"
	"github.com/idsproject/iris/util"
)

func (app *Application) routes() (http.Handler, error) {
	spec, err := api.GetSpec()
	if err != nil {
		return nil, fmt.Errorf("routes/api/GetSpec: %w", err)
	}

	mux := http.NewServeMux()

	api.HandlerWithOptions(handler.CreateServer(app.Logger, app.Queries), api.StdHTTPServerOptions{
		BaseRouter:       mux,
		Middlewares:      []api.MiddlewareFunc{app.validateRequests(spec)},
		ErrorHandlerFunc: app.paramError,
	})

	// app.authenticate and app.requireAuthenticatedUser would wrap mux here.

	return app.recoverer(mux), nil
}

// paramError reports a path, query or header parameter that the generated
// wrapper couldn't bind. It runs before validation and the handler.
func (app *Application) paramError(w http.ResponseWriter, r *http.Request, err error) {
	app.Logger.Warn("invalid request parameter", "method", r.Method, "path", r.URL.Path, "err", err)
	writeErr := util.Error(w, r, http.StatusBadRequest, err.Error())
	if writeErr != nil {
		app.Logger.Error("paramError/util/Error", "err", writeErr)
	}
}
