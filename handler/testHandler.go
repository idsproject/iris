//go:build ignore

package handler

import (
	"log/slog"
	"net/http"
	"os"
	"path/filepath"

	"github.com/idsproject/iris/aws"
	"github.com/idsproject/iris/util"

	"github.com/idsproject/iris/data"

	"github.com/go-chi/chi/v5"
)

type TestHandler struct {
	Logger  *slog.Logger
	Queries *data.Queries
}

func CreateTestHandler(logger *slog.Logger, queries *data.Queries) *TestHandler {
	return &TestHandler{
		Logger:  logger,
		Queries: queries,
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
		handler.Logger.Error("HandleTest/os/Getwd", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.Logger.Error("HandleTestToAws/util/Error", "err", err)
		}
		return
	}

	filePath := workingDir + "/test_pdfs/" + filepath.Base(fileName)

	file, err := os.Open(filePath) // #nosec G304
	if err != nil {
		handler.Logger.Error("HandleTestToAws/os/Open", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.Logger.Error("HandleTestToAws/util/Error", "err", err)
		}
		return
	}

	err = aws.UploadArticle(file, fileName, nil)
	if err != nil {
		handler.Logger.Error("HandleTest/aws/UploadArticle", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.Logger.Error("HandleTestToAws/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, "success")
	if err != nil {
		handler.Logger.Error("HandleTestToAws/util/Success", "err", err)
	}
}

func (handler *TestHandler) HandleTestFromAws(w http.ResponseWriter, r *http.Request) {
	fileName := r.URL.Query().Get("name")

	err := aws.DownloadArticle(fileName)
	if err != nil {
		handler.Logger.Error("HandleTestFromAws/aws/DownloadArticle", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, err)
		if err != nil {
			handler.Logger.Error("HandleTestFromAws/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, "success")
	if err != nil {
		handler.Logger.Error("HandleTestFromAws/util/Success", "err", err)
	}
}

func (handler *TestHandler) HandleTestList(w http.ResponseWriter, r *http.Request) {
	objects, err := aws.ListObjects()
	if err != nil {
		handler.Logger.Error("HandleTestList/aws/ListObjects", "err", err)
		err = util.Error(w, r, http.StatusInternalServerError, "err")
		if err != nil {
			handler.Logger.Error("HandleTestList/util/Error", "err", err)
		}
		return
	}

	err = util.Success(w, r, objects)
	if err != nil {
		handler.Logger.Error("HandleTestList/util/Success", "err", err)
	}
}
