package util

import (
	"encoding/json"
	"errors"
	"fmt"
	"log/slog"
	"net/http"
	"strings"
)

type Response struct {
	Content map[string]any
}

type ResponseData struct {
	Writer  http.ResponseWriter
	Request *http.Request
	Logger  *slog.Logger
}
type ResponseMessage struct {
	Error    error
	Message  string
	CallPath string
	Status   int
}

func CreateResponse() *Response {
	var resp Response
	resp.Content = make(map[string]any)
	return &resp
}

func (resp *Response) Add(key string, value any) {
	resp.Content[key] = value
}

func (resp *Response) Get(key string) any {
	return resp.Content[key]
}

func (resp *Response) Clear() {
	clear(resp.Content)
}

func (resp *Response) WriteResponse(w http.ResponseWriter, r *http.Request, status int) error {
	result, err := json.Marshal(resp.Content)
	if err != nil {
		w.WriteHeader(http.StatusInternalServerError)
		_, writeErr := w.Write([]byte(`{"status":"error","message":"Couldn't marshal response"}`))
		if writeErr != nil {
			return errors.Join(err, writeErr)
		}
		return err
	}

	w.Header().Set("Content-Type", "application/json")
	w.Header().Set("Cache-Control", "no-store")
	w.WriteHeader(status)

	_, err = w.Write(result)
	if err != nil {
		return err
	}

	return nil
}

func Error(w http.ResponseWriter, r *http.Request, status int, message any, args ...any) error {
	resp := CreateResponse()
	resp.Add("status", "error")
	if str, ok := message.(string); ok && len(args) > 0 {
		resp.Add("message", fmt.Sprintf(str, args...))
	} else {
		resp.Add("message", message)
	}
	err := resp.WriteResponse(w, r, status)
	if err != nil {
		return err
	}
	return nil
}

func Success(w http.ResponseWriter, r *http.Request, message any, args ...any) error {
	resp := CreateResponse()
	resp.Add("status", "success")
	if str, ok := message.(string); ok && len(args) > 0 {
		resp.Add("message", fmt.Sprintf(str, args...))
	} else {
		resp.Add("message", message)
	}
	err := resp.WriteResponse(w, r, http.StatusOK)
	if err != nil {
		return err
	}
	return nil
}

func EndpointError(data ResponseData, message ResponseMessage) {
	baseFunc, _, _ := strings.Cut(message.CallPath, "/")
	data.Logger.Error(message.CallPath, "err", message.Error)
	err := Error(data.Writer, data.Request, message.Status, message.Message)
	if err != nil {
		data.Logger.Error(baseFunc+"/util/Error", "err", err)
	}
}
