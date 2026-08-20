package aws

import (
    "errors"
	"context"
	"log/slog"
    "os"

	"github.com/aws/aws-sdk-go-v2/aws"
	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
)

var ErrLoadConfig = errors.New("error loading AWS SDK config")
var ErrCallS3 = errors.New("error calling S3")

type AwsS3Connection struct {
    ctx context.Context
    awsConfig aws.Config
    s3Client *s3.Client
}

func NewAwsS3Connection(ctx context.Context) (*AwsS3Connection, error) {
    if ctx == nil {
        ctx = context.WithValue(context.Background(), slog.New(slog.NewTextHandler(os.Stdout, nil)), "logger")
    }

    sdkConfig, err := config.LoadDefaultConfig(ctx)
    if err != nil {
        return nil, errors.Join(ErrLoadConfig, err)
    }

    client := s3.NewFromConfig(sdkConfig)
    
    return &AwsS3Connection {
        ctx: ctx,
        awsConfig: sdkConfig,
        s3Client: client,
    }, nil
}

func (conn *AwsS3Connection) GetBuckets() ([]string, error) {
	result, err := conn.s3Client.ListBuckets(conn.ctx, &s3.ListBucketsInput{})
	if err != nil {
		return nil, errors.Join(ErrCallS3, err)
	}

	buckets := make([]string, 0)
	for _, bucket := range result.Buckets {
		buckets = append(buckets, *bucket.Name)
	}

	return buckets, nil
}
