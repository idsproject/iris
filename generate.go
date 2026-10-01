package main

//go:generate go tool sqlc generate
//go:generate go tool oapi-codegen -config openapi/oapi-codegen.yaml openapi/open-api.yaml
