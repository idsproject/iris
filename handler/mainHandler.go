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

const maxUploadFileSize = 700 // in MB
const fileSizeThreshold = 50  // in MB

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
	router.Get("/healthz", handler.HandleHealthz)
	router.Post("/upload", handler.HandleUpload)
    router.Get("/status/{filename}", handler.HandleStatus)
    router.Get("/download/{filename}", handler.HandleDownload)
	router.Post("/notify", handler.HandleNotify)
}

func (handler *MainHandler) HandleHealthz(w http.ResponseWriter, r *http.Request) {
	_, err := w.Write([]byte("OK"))
	if err != nil {
		handler.GetLogger().Error("HandleHealthz/http/Write", "err", err)
	}
}

func (handler *MainHandler) HandleNotify(w http.ResponseWriter, r *http.Request) {
	handler.GetLogger().Info("Notified")
}

func (handler *MainHandler) HandleUpload(w http.ResponseWriter, r *http.Request) {
	responseData := util.ResponseData{
		Writer:  w,
		Request: r,
		Logger:  handler.GetLogger(),
	}
	var responseMessage util.ResponseMessage

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadFileSize<<20)
	err := r.ParseMultipartForm(fileSizeThreshold << 20) // #nosec G120
	if err != nil {
		responseMessage = util.ResponseMessage{
			Status:   http.StatusBadRequest,
			Message:  "Could not parse data",
			Error:    err,
			CallPath: "HandleUpload/http/ParseMultipartForm",
		}
		util.EndpointError(responseData, responseMessage)
		return
	}

	file, fileHeader, err := r.FormFile("file")
	if err != nil {
		responseMessage = util.ResponseMessage{
			Status:   http.StatusBadRequest,
			Message:  "Could not parse file",
			Error:    err,
			CallPath: "HandleUpload/http/FileForm",
		}
		util.EndpointError(responseData, responseMessage)
		return
	}
	defer file.Close() //nolint:errcheck

	err = aws.UploadArticle(file, fileHeader.Filename, &fileHeader.Size)
	if err != nil {
		responseMessage = util.ResponseMessage{
			Status:   http.StatusInternalServerError,
			Message:  "Error uploading to AWS",
			Error:    err,
			CallPath: "HandleUpload/aws/UploadArticle",
		}
		util.EndpointError(responseData, responseMessage)
		return
	}

	err = util.Success(w, r, "uploaded")
	if err != nil {
		handler.GetLogger().Error("HandleUpload/util/Success", "err", err)
	}
}

func (handler *MainHandler) HandleStatus(w http.ResponseWriter, r *http.Request) {
	responseData := util.ResponseData{
		Writer:  w,
		Request: r,
		Logger:  handler.GetLogger(),
	}
	var responseMessage util.ResponseMessage

    fileName := chi.URLParam(r, "filename")
    resultKeyName := "result/COMPLIANT_" + fileName

    objects, err := aws.ListObjects()
    if err != nil {
		responseMessage = util.ResponseMessage{
			Status:   http.StatusInternalServerError,
			Message:  "Error listing AWS objects",
			Error:    err,
			CallPath: "HandleDownload/aws/ListObjects",
		}
		util.EndpointError(responseData, responseMessage)
		return
    }

    response := util.CreateResponse()

    for _, object := range objects {
        if object.Key == resultKeyName {
            response.Add("status", "success")
            response.Add("message", "file found")

            response.WriteResponse(w, r, http.StatusOK)
            return
        }
    }

    response.Add("status", "error")
    response.Add("message", "file not found")

    response.WriteResponse(w, r, http.StatusOK)
}

func (handler *MainHandler) HandleDownload(w http.ResponseWriter, r *http.Request) {
	responseData := util.ResponseData{
		Writer:  w,
		Request: r,
		Logger:  handler.GetLogger(),
	}
	var responseMessage util.ResponseMessage

    fileName := chi.URLParam(r, "filename")

    err := aws.DownloadArticle(fileName)
    if err != nil {
        responseMessage = util.ResponseMessage{
            Status:   http.StatusInternalServerError,
            Message:  "Error downloading to AWS",
            Error:    err,
            CallPath: "HandleUpload/aws/DownloadArticle",
        }
        util.EndpointError(responseData, responseMessage)
        return
    }
    http.ServeFile(w, r, os.Getenv("DOWNLOAD_DIR") + "/" + fileName)
}
