package handler

import (
	"context"
	"errors"
	"fmt"
	"io/fs"
	"log/slog"
	"net/http"
	"os"
	"path/filepath"
	"strings"

	"github.com/idsproject/iris/api"
	"github.com/idsproject/iris/aws"
	"github.com/idsproject/iris/util"

	"github.com/idsproject/iris/data"

	"github.com/ledongthuc/pdf"
)

const maxUploadFileSize = 700 // in MB
const fileSizeThreshold = 50  // in MB

const PricePerPage = 0.15

// errRawOperation is returned by the strict stubs for operations that
// [server] routes to raw net/http handlers instead.
var errRawOperation = errors.New("operation is served by a raw handler")

// MainHandler implements the operations in openapi/open-api.yaml.
//
// Most operations satisfy [api.StrictServerInterface]: they take a typed
// request and return one of the responses the spec allows for them.
// UploadArticle and DownloadArticle need the raw *http.Request, for
// ParseMultipartForm and for http.ServeFileFS's Range and conditional
// handling, so [server] sends them to uploadArticle and downloadArticle.
type MainHandler struct {
	Logger  *slog.Logger
	Queries *data.Queries
}

// CreateServer returns the handlers for [api.HandlerWithOptions].
func CreateServer(logger *slog.Logger, queries *data.Queries) api.ServerInterface {
	handler := &MainHandler{
		Logger:  logger,
		Queries: queries,
	}

	strict := api.NewStrictHandlerWithOptions(handler, []api.StrictMiddlewareFunc{noStore}, api.StrictHTTPServerOptions{
		RequestErrorHandlerFunc:  handler.requestError,
		ResponseErrorHandlerFunc: handler.responseError,
	})

	return &server{ServerInterface: strict, handler: handler}
}

// server serves every operation through the strict wrapper except the two
// that need the raw request, which it overrides.
type server struct {
	api.ServerInterface
	handler *MainHandler
}

func (s *server) UploadArticle(w http.ResponseWriter, r *http.Request, params api.UploadArticleParams) {
	s.handler.uploadArticle(w, r, params)
}

func (s *server) DownloadArticle(w http.ResponseWriter, r *http.Request, filename api.FilenamePath) {
	s.handler.downloadArticle(w, r, filename)
}

func (handler *MainHandler) GetHealthz(_ context.Context, _ api.GetHealthzRequestObject) (api.GetHealthzResponseObject, error) {
	return api.GetHealthz200TextResponse("OK"), nil
}

func (handler *MainHandler) GetV1Healthz(_ context.Context, _ api.GetV1HealthzRequestObject) (api.GetV1HealthzResponseObject, error) {
	return api.GetV1Healthz200TextResponse("OK"), nil
}

func (handler *MainHandler) GetRobotsTxt(_ context.Context, _ api.GetRobotsTxtRequestObject) (api.GetRobotsTxtResponseObject, error) {
	robots, err := os.ReadFile("./robots.txt")
	if err != nil {
		return nil, fmt.Errorf("GetRobotsTxt/os/ReadFile: %w", err)
	}

	return api.GetRobotsTxt200TextResponse(robots), nil
}

func (handler *MainHandler) Notify(_ context.Context, request api.NotifyRequestObject) (api.NotifyResponseObject, error) {
	message := *request.Body

	safePath, ok := safeDownloadPath(message.File)
	if !ok {
		handler.Logger.Warn("Notify received unclean filename", "filename", message.File)
		return api.Notify400JSONResponse{ErrorJSONResponse: errorBody("Invalid file name")}, nil
	}

	switch message.Message {
	case api.RemediationComplete:
		// here is where we will call CrossLink
		handler.Logger.Info("Received remediation complete", "payload", message)
		err := aws.DownloadArticle(message.File)
		if err != nil {
			handler.Logger.Error("Notify/aws/DownloadArticle", "err", err)
			return api.Notify500JSONResponse(errorBody("Error downloading to AWS")), nil
		}
	case api.DownloadComplete:
		err := os.Remove(safePath) // #nosec G703
		if err != nil {
			handler.Logger.Error("Notify/os/Remove", "err", err)
			return api.Notify500JSONResponse(errorBody("Unable to clean up files")), nil
		}
	case api.RemediationError:
		handler.Logger.Info("remedation error received", "payload", message)
		file, err := os.Create(safePath + ".error") // #nosec G304 G703
		if err != nil {
			handler.Logger.Error("Notify/os/Create", "err", err)
			return api.Notify500JSONResponse(errorBody("Unable to create error file")), nil
		}
		err = file.Close()
		if err != nil {
			handler.Logger.Error("Notify/os/Close", "err", err)
			return api.Notify500JSONResponse(errorBody("Unable to create error file")), nil
		}
	default:
		return api.Notify400JSONResponse{ErrorJSONResponse: errorBody("Unknown message")}, nil
	}

	return api.Notify200JSONResponse{SuccessJSONResponse: successBody("notified")}, nil
}

func (handler *MainHandler) GetArticleStatus(_ context.Context, request api.GetArticleStatusRequestObject) (api.GetArticleStatusResponseObject, error) {
	safePath, ok := safeDownloadPath(request.Filename)
	if !ok {
		handler.Logger.Warn("GetArticleStatus received unclean filename", "filename", request.Filename)
		return api.GetArticleStatus400JSONResponse{ErrorJSONResponse: errorBody("Invalid file name")}, nil
	}

	_, err := os.Stat(safePath) // #nosec G703
	if err == nil {
		return api.GetArticleStatus200JSONResponse{
			Status:  api.ResponseStatusSuccess,
			Message: "file found",
			Done:    true,
		}, nil
	}
	if !errors.Is(err, os.ErrNotExist) {
		handler.Logger.Error("GetArticleStatus/os/Stat", "err", err)
		return api.GetArticleStatus500JSONResponse(errorBody("error checking file")), nil
	}

	_, err = os.Stat(safePath + ".error") // #nosec G703
	switch {
	case err == nil:
		return api.GetArticleStatus200JSONResponse{
			Status:           api.ResponseStatusSuccess,
			Message:          "error during remediation",
			Done:             false,
			RemediationError: new(true),
		}, nil
	case errors.Is(err, os.ErrNotExist):
		return api.GetArticleStatus200JSONResponse{
			Status:  api.ResponseStatusSuccess,
			Message: "file not found",
			Done:    false,
		}, nil
	default:
		handler.Logger.Error("GetArticleStatus/os/Stat", "err", err)
		return api.GetArticleStatus500JSONResponse(errorBody("error checking error file")), nil
	}
}

func (handler *MainHandler) GetReport(ctx context.Context, request api.GetReportRequestObject) (api.GetReportResponseObject, error) {
	rows, err := handler.Queries.GetReportFromRange(ctx, data.GetReportFromRangeParams{
		Libraryid:   request.Params.XLibraryId,
		Processed:   request.Params.Start.Time,
		Processed_2: request.Params.End.Time,
	})
	if err != nil {
		handler.Logger.Error("GetReport/data/GetReportFromRange", "err", err)
		return api.GetReport500JSONResponse(errorBody("error getting report data")), nil
	}

	report := api.Report{Data: make([]api.Tracking, 0, len(rows))}
	for _, row := range rows {
		report.TotalPages += int(row.Pagecount)
		report.Data = append(report.Data, api.Tracking{
			Processed:     row.Processed,
			LibraryId:     row.Libraryid,
			TransactionId: row.Transactionid,
			PageCount:     int(row.Pagecount),
			Paid:          row.Paid,
		})
	}
	report.TotalCost = float64(report.TotalPages) * PricePerPage

	return api.GetReport200JSONResponse{
		Status:  api.ResponseStatusSuccess,
		Message: "report generated",
		Result:  report,
	}, nil
}

// UploadArticle is served by uploadArticle; see [server].
func (handler *MainHandler) UploadArticle(_ context.Context, _ api.UploadArticleRequestObject) (api.UploadArticleResponseObject, error) {
	return nil, errRawOperation
}

// DownloadArticle is served by downloadArticle; see [server].
func (handler *MainHandler) DownloadArticle(_ context.Context, _ api.DownloadArticleRequestObject) (api.DownloadArticleResponseObject, error) {
	return nil, errRawOperation
}

func (handler *MainHandler) uploadArticle(w http.ResponseWriter, r *http.Request, params api.UploadArticleParams) {
	w.Header().Set("Cache-Control", "no-store")

	r.Body = http.MaxBytesReader(w, r.Body, maxUploadFileSize<<20)
	err := r.ParseMultipartForm(fileSizeThreshold << 20) // #nosec G120
	if err != nil {
		handler.Logger.Error("UploadArticle/http/ParseMultipartForm", "err", err)
		handler.writeUpload(w, api.UploadArticle400JSONResponse{ErrorJSONResponse: errorBody("Could not parse data")})
		return
	}

	file, fileHeader, err := r.FormFile("file")
	if err != nil {
		handler.Logger.Error("UploadArticle/http/FormFile", "err", err)
		handler.writeUpload(w, api.UploadArticle400JSONResponse{ErrorJSONResponse: errorBody("Could not parse file")})
		return
	}
	defer file.Close() //nolint:errcheck

	pdfReader, err := pdf.NewReader(file, fileHeader.Size)
	if err != nil {
		handler.Logger.Error("UploadArticle/pdf/NewReader", "err", err)
		handler.writeUpload(w, api.UploadArticle500JSONResponse(errorBody("Error opening as pdf")))
		return
	}

	pageCount := int32(pdfReader.NumPage()) //#nosec G115

	err = aws.UploadArticle(file, fileHeader.Filename, &fileHeader.Size)
	if err != nil {
		handler.Logger.Error("UploadArticle/aws/UploadArticle", "err", err)
		handler.writeUpload(w, api.UploadArticle500JSONResponse(errorBody("Error uploading to AWS")))
		return
	}

	_, err = handler.Queries.InsertTracking(r.Context(), data.InsertTrackingParams{
		Libraryid:     params.XLibraryId,
		Transactionid: params.Transaction,
		Pagecount:     pageCount,
	})
	if err != nil {
		handler.Logger.Error("UploadArticle/data/InsertTracking", "err", err)
		handler.writeUpload(w, api.UploadArticle500JSONResponse(errorBody("Error updating tracking")))
		return
	}

	handler.writeUpload(w, api.UploadArticle200JSONResponse{SuccessJSONResponse: successBody("uploaded")})
}

func (handler *MainHandler) writeUpload(w http.ResponseWriter, response api.UploadArticleResponseObject) {
	err := response.VisitUploadArticleResponse(w)
	if err != nil {
		handler.Logger.Error("UploadArticle/api/VisitUploadArticleResponse", "err", err)
	}
}

func (handler *MainHandler) downloadArticle(w http.ResponseWriter, r *http.Request, filename api.FilenamePath) {
	err := aws.DownloadArticle(filename)
	if err != nil {
		handler.Logger.Error("DownloadArticle/aws/DownloadArticle", "err", err)
		handler.writeDownloadError(w, api.DownloadArticle500JSONResponse(errorBody("Error downloading to AWS")))
		return
	}

	dirFS := os.DirFS(os.Getenv("DOWNLOAD_DIR"))

	// ServeFileFS answers a missing file with a plain-text 404, so check first
	// to return the JSON envelope instead. An invalid name or a directory is
	// treated as missing too, since neither is a file that can be downloaded.
	info, err := fs.Stat(dirFS, filename)
	if errors.Is(err, fs.ErrNotExist) || errors.Is(err, fs.ErrInvalid) || (err == nil && info.IsDir()) {
		handler.writeDownloadError(w, api.DownloadArticle404JSONResponse{ErrorJSONResponse: errorBody("file not found")})
		return
	}
	if err != nil {
		handler.Logger.Error("DownloadArticle/fs/Stat", "err", err)
		handler.writeDownloadError(w, api.DownloadArticle500JSONResponse(errorBody("Error checking file")))
		return
	}

	http.ServeFileFS(w, r, dirFS, filename) // #nosec G703
}

func (handler *MainHandler) writeDownloadError(w http.ResponseWriter, response api.DownloadArticleResponseObject) {
	w.Header().Set("Cache-Control", "no-store")
	err := response.VisitDownloadArticleResponse(w)
	if err != nil {
		handler.Logger.Error("DownloadArticle/api/VisitDownloadArticleResponse", "err", err)
	}
}

// requestError reports a request the strict wrapper couldn't decode, such as
// a malformed JSON body.
func (handler *MainHandler) requestError(w http.ResponseWriter, r *http.Request, err error) {
	handler.Logger.Warn("strict request error", "path", r.URL.Path, "err", err)
	writeErr := util.Error(w, r, http.StatusBadRequest, err.Error())
	if writeErr != nil {
		handler.Logger.Error("requestError/util/Error", "err", writeErr)
	}
}

// responseError reports a strict handler that returned an error instead of a
// response, or a response that failed to write.
func (handler *MainHandler) responseError(w http.ResponseWriter, r *http.Request, err error) {
	handler.Logger.Error("strict response error", "path", r.URL.Path, "err", err)
	writeErr := util.Error(w, r, http.StatusInternalServerError, "Internal server error")
	if writeErr != nil {
		handler.Logger.Error("responseError/util/Error", "err", writeErr)
	}
}

// noStore keeps gateways and clients from caching API responses, most
// importantly the status polled while a file is remediated.
func noStore(f api.StrictHandlerFunc, _ string) api.StrictHandlerFunc {
	return func(ctx context.Context, w http.ResponseWriter, r *http.Request, request any) (any, error) {
		w.Header().Set("Cache-Control", "no-store")
		return f(ctx, w, r, request)
	}
}

// safeDownloadPath joins name onto $DOWNLOAD_DIR, reporting false if the
// result would land outside it.
func safeDownloadPath(name string) (string, bool) {
	baseDir := os.Getenv("DOWNLOAD_DIR")
	safePath := filepath.Join(baseDir, name)
	return safePath, strings.HasPrefix(safePath, baseDir)
}

// errorBody builds the envelope shared by every JSON error response. The
// generated 4xx types embed api.ErrorJSONResponse while the 5xx types are
// defined on api.BaseResponse, so callers wrap or convert it accordingly.
func errorBody(message string) api.ErrorJSONResponse {
	return api.ErrorJSONResponse{Status: api.ResponseStatusError, Message: message}
}

func successBody(message string) api.SuccessJSONResponse {
	return api.SuccessJSONResponse{Status: api.ResponseStatusSuccess, Message: message}
}
