package handler

import (
	"log/slog"
	"net/http"
	"os"

	"github.com/idsproject/iris/aws"
	// "github.com/idsproject/iris/util"

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
	router.Get("/buckets", handler.HandleBuckets)
	router.Post("/upload", handler.HandleUpload)
	router.Post("/test", handler.HandleTest)
}

func (handler *MainHandler) HandleBuckets(w http.ResponseWriter, r *http.Request) {
	// buckets, err := aws.GetBuckets(handler.Logger)
	// if err != nil {
	// 	handler.Logger.Error("HandleBuckets/aws/GetBuckets", "err", err)
	// 	err = util.Error(w, r, http.StatusInternalServerError, "Can't get buckets")
	// 	if err != nil {
	// 		handler.Logger.Error("HandleBuckets/util/Error", "err", err)
	// 	}
	// 	return
	// }

	// err = util.Success(w, r, buckets)
	// if err != nil {
	// 	handler.Logger.Error("HandleBuckets/util/Success", "err", err)
	// }
}

func (handler *MainHandler) HandleUpload(w http.ResponseWriter, r *http.Request) {
	w.Write([]byte("uploading")) //nolint:errcheck,gosec
}

func (handler *MainHandler) HandleTest(w http.ResponseWriter, r *http.Request) {
	fileName := r.URL.Query().Get("name")

	workingDir, err := os.Getwd()
	if err != nil {
		handler.GetLogger().Error("HandleTest/os/Getwd", "err", err)
		w.Write([]byte("error")) //nolint:errcheck,gosec
		return
	}

	filePath := workingDir + "/test_pdfs/" + fileName

	err = aws.UploadArticle(filePath)
	if err != nil {
		handler.GetLogger().Error("HandleTest/aws/UploadArticle", "err", err)
		w.Write([]byte("error")) //nolint:errcheck,gosec
		return
	}

	w.Write([]byte("success")) //nolint:errcheck,gosec
}
