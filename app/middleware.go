package app

import (
	"context"
	"errors"
	"mime"
	"net/http"
	"runtime/debug"
	"strings"

	"github.com/idsproject/iris/api"
	"github.com/idsproject/iris/util"

	"github.com/getkin/kin-openapi/openapi3"
	"github.com/getkin/kin-openapi/openapi3filter"
	nethttpmiddleware "github.com/oapi-codegen/nethttp-middleware"
)

// recoverer turns a panicking handler into a logged 500 instead of a dropped
// connection. http.ErrAbortHandler is re-panicked because net/http uses it to
// abort a response on purpose.
func (app *Application) recoverer(next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		defer func() {
			rvr := recover()
			if rvr == nil {
				return
			}
			if err, ok := rvr.(error); ok && errors.Is(err, http.ErrAbortHandler) {
				panic(rvr)
			}

			app.Logger.Error("recovered panic", "method", r.Method, "path", r.URL.Path, "panic", rvr, "stack", string(debug.Stack()))
			err := util.Error(w, r, http.StatusInternalServerError, "Internal server error")
			if err != nil {
				app.Logger.Error("recoverer/util/Error", "err", err)
			}
		}()

		next.ServeHTTP(w, r)
	})
}

// validateRequests checks each request against the OpenAPI spec before its
// handler runs. It is attached per operation by api.HandlerWithOptions, so
// routes outside the spec are never validated.
func (app *Application) validateRequests(spec *openapi3.T) api.MiddlewareFunc {
	options := nethttpmiddleware.Options{
		Options: openapi3filter.Options{
			// The gateway in front of IRIS authenticates callers; the spec's
			// ApiKeyAuth scheme documents that contract rather than one IRIS enforces.
			AuthenticationFunc: openapi3filter.NoopAuthenticationFunc,
		},
		// The spec's servers are the public hosts, and matching a request's
		// Host against them fails behind the gateway.
		DoNotValidateServers: true,
		ErrorHandlerWithOpts: app.validationError,
	}
	validate := nethttpmiddleware.OapiRequestValidatorWithOptions(spec, &options)

	// Validating a multipart body makes kin-openapi read all of it into
	// memory, up to 700 MB for an upload. Those requests still have their
	// parameters validated but leave the body to the handler.
	noBodyOptions := options
	noBodyOptions.Options.ExcludeRequestBody = true
	validateNoBody := nethttpmiddleware.OapiRequestValidatorWithOptions(spec, &noBodyOptions)

	return func(next http.Handler) http.Handler {
		withBody, withoutBody := validate(next), validateNoBody(next)
		return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
			if isMultipart(r) {
				withoutBody.ServeHTTP(w, r)
				return
			}
			withBody.ServeHTTP(w, r)
		})
	}
}

func (app *Application) validationError(_ context.Context, err error, w http.ResponseWriter, r *http.Request, opts nethttpmiddleware.ErrorHandlerOpts) {
	// kin-openapi follows the reason with the offending schema and value on
	// later lines; only the first is meant for the caller.
	message, _, _ := strings.Cut(err.Error(), "\n")

	app.Logger.Warn("request failed validation", "method", r.Method, "path", r.URL.Path, "err", err)
	writeErr := util.Error(w, r, opts.StatusCode, message)
	if writeErr != nil {
		app.Logger.Error("validationError/util/Error", "err", writeErr)
	}
}

func isMultipart(r *http.Request) bool {
	mediaType, _, err := mime.ParseMediaType(r.Header.Get("Content-Type"))
	return err == nil && mediaType == "multipart/form-data"
}
