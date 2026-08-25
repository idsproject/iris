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

type TestHandler struct {
	Logger    *slog.Logger
	LogsModel *data.LogsModel
}

func (handler *TestHandler) GetLogger() *slog.Logger {
	return handler.Logger
}

func (handler *TestHandler) GetLogsModel() *data.LogsModel {
	return handler.LogsModel
}

func CreateTestHandler(logger *slog.Logger, logsModel *data.LogsModel) *TestHandler {
	return &TestHandler{
		Logger:    logger,
		LogsModel: logsModel,
	}
}

func (handler *TestHandler) Routes(router chi.Router) {
	router.Post("/awsupload", handler.HandleTestToAws)
	router.Get("/awslist", handler.HandleTestList)
	router.Post("/awsdownload", handler.HandleTestFromAws)
}

func (handler *TestHandler) HandleTestToAws(w http.ResponseWriter, r *http.Request) {
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

func (handler *TestHandler) HandleTestFromAws(w http.ResponseWriter, r *http.Request) {
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

func (handler *TestHandler) HandleTestList(w http.ResponseWriter, r *http.Request) {
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
