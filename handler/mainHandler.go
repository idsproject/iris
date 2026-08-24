package handler

import (
	"log/slog"
	"net/http"
	"os"

	"github.com/idsproject/iris/aws"
	"github.com/idsproject/iris/util"

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
	router.Post("/testupload", handler.HandleTestToAws)
	router.Get("/testlist", handler.HandleTestList)
	router.Post("/testdownload", handler.HandleTestFromAws)
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

func (handler *MainHandler) HandleTestToAws(w http.ResponseWriter, r *http.Request) {
	fileName := r.URL.Query().Get("name")

	workingDir, err := os.Getwd()
	if err != nil {
		handler.GetLogger().Error("HandleTest/os/Getwd", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.GetLogger().Error("HandleTestToAws/util/Error", "err", err)
		}
		return
	}

	filePath := workingDir + "/test_pdfs/" + fileName

	err = aws.UploadArticle(filePath)
	if err != nil {
		handler.GetLogger().Error("HandleTest/aws/UploadArticle", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.GetLogger().Error("HandleTestToAws/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, "success")
	if err != nil {
		handler.GetLogger().Error("HandleTestToAws/util/Success", "err", err)
	}
}

func (handler *MainHandler) HandleTestFromAws(w http.ResponseWriter, r *http.Request) {
	fileName := r.URL.Query().Get("name")

	err := aws.DownloadArticle(fileName)
	if err != nil {
		handler.GetLogger().Error("HandleTestFromAws/aws/DownloadArticle", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, err)
		if err != nil {
			handler.GetLogger().Error("HandleTestFromAws/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, "success")
	if err != nil {
		handler.GetLogger().Error("HandleTestFromAws/util/Success", "err", err)
	}
}

func (handler *MainHandler) HandleTestList(w http.ResponseWriter, r *http.Request) {
	// fileName := r.URL.Query().Get("name")

	objects, err := aws.ListObjects()
	if err != nil {
		handler.GetLogger().Error("HandleTestList/aws/ListObjects", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.GetLogger().Error("HandleTestList/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, objects)
	if err != nil {
		handler.GetLogger().Error("HandleTestList/util/Success", "err", err)
	}
}
