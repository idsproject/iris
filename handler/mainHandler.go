package handler

import (
	"log/slog"
	"net/http"

	"github.com/idsproject/iris/internal/data"

	"github.com/go-chi/chi/v5"
)

type MainHandler struct {
	Logger    *slog.Logger
	LogsModel *data.LogsModel
}

func (handler *MainHandler) GetLogger() *slog.Logger {
	return handler.Logger
}

func (handler *MainHandler) GetLogsModel() *data.LogsModel {
	return handler.LogsModel
}

func CreateMainHandler(logger *slog.Logger, logsModel *data.LogsModel) *MainHandler {
	return &MainHandler{
		Logger:    logger,
		LogsModel: logsModel,
	}
}

func (handler *MainHandler) Routes(router chi.Router) {
	router.Post("/upload", handler.HandleUpload)
}

func (handler *MainHandler) HandleUpload(w http.ResponseWriter, r *http.Request) {
	w.Write([]byte("uploading")) //nolint:errcheck,gosec
}
