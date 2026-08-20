package aws

import (
	"context"
	// "errors"
	"fmt"
	"log/slog"

	"github.com/aws/aws-sdk-go-v2/config"
	"github.com/aws/aws-sdk-go-v2/service/s3"
	// "github.com/aws/smithy-go"
)

func GetBuckets(logger *slog.Logger) ([]string, error) {
	ctx := context.Background()
	sdkConfig, err := config.LoadDefaultConfig(ctx)
	if err != nil {
		return nil, fmt.Errorf("couldn't load AWS config: %w", err)
	}

	s3Client := s3.NewFromConfig(sdkConfig)
	result, err := s3Client.ListBuckets(ctx, &s3.ListBucketsInput{})
	if err != nil {
		// var ae smithy.APIError
		// if errors.As(err, &ae) && ae.ErrorCode() == "AccessDenied" {
		// 	fmt.Println("You don't have permission to list buckets for this account.")
		// } else {
		// 	fmt.Printf("Couldn't list buckets for your account. Here's why: %v\n", err)
		// }
		return nil, fmt.Errorf("error calling S3: %w", err)
	}

	buckets := make([]string, 0)
	for _, bucket := range result.Buckets {
		buckets = append(buckets, *bucket.Name)
	}

	return buckets, nil
}
