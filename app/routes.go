package app

import (
	"net/http"

	"github.com/idsproject/iris/handler"

	"github.com/go-chi/chi/v5"
	"github.com/go-chi/chi/v5/middleware"
)

func (app *Application) routes() http.Handler {
	mainHandler := handler.CreateMainHandler(app.Logger, &app.Models.Logs)

	router := chi.NewRouter()

	router.Use(middleware.RequestID)
	router.Use(middleware.ClientIPFromHeader("X-Real-IP"))
	router.Use(middleware.Recoverer)
	// router.Use(app.authenticate)

	router.Get("/healthz", HandleHealthz)
	router.Get("/robots.txt", ServeRobots)

	router.Route("/v1", func(router chi.Router) {
		// router.Use(app.requireAuthenticatedUser)
		router.Group(mainHandler.Routes)
	})

	return router
}

func HandleHealthz(w http.ResponseWriter, r *http.Request) {
	w.Write([]byte("OK")) //nolint:errcheck,gosec
}

func ServeRobots(w http.ResponseWriter, r *http.Request) {
	http.ServeFile(w, r, "./robots.txt")
}
