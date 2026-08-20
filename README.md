<a id="readme-top"></a>

<!-- logo -->
<br />
<div align="center">
  <a href="https://github.com/idsproject/iris">
    <img src="images/logo.png" width="300" alt="Logo">
  </a>

  <h3 align="center">IRIS</h3>
</div>

<!-- table of contents -->
<details>
  <summary>Table of Contents</summary>
  <ol>
    <li>
      <a href="#about-the-project">About The Project</a>
      <ul>
        <li><a href="#built-with">Built With</a></li>
      </ul>
    </li>
    <li>
      <a href="#getting-started">Getting Started</a>
      <ul>
        <li><a href="#prerequisites">Prerequisites</a></li>
        <li><a href="#installation">Installation</a></li>
      </ul>
    </li>
    <li><a href="#usage">Usage</a></li>
    <li><a href="#roadmap">Roadmap</a></li>
    <li><a href="#contact">Contact</a></li>
  </ol>
</details>

<!-- about -->
## About The Project

This is the API to access the IRIS remediation server. It handles input/output to various systems, standardizing how its accessed. It will do some pre-processing with things like estimated cost, etc but the actual remediation work will be done on the AWS system.

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- built with -->
### Built With

* [![Go][Go.dev]][Go-url]
* [![Chi][Chi.com]][Chi-url]
* [![Pgx][Pgx.com]][Pgx-url]

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- getting started -->
## Getting Started

Here is how to get started

<!-- prereqs -->
### Prerequisites

* AWS with credentials installed
* Docker

<!-- install -->
### Installation

Here is how to install

#### Configuration

| Name         | Description         | Default value       |
|--------------|---------------------|---------------------|
| `HTTP_PORT`  | Server port         | `8080`              |
| `DB_URL`     | URL for the DB      |                     |

#### Running

```
docker build -t iris
docker run iris
```

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- usage -->
## Usage

Here are the endpoints this API exposes

[![OpenAPI][Openapi.com]](https://swagger.io)

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- roadmap -->
## Roadmap

- [ ] Foundation
    - [ ] Basic structure
    - [ ] Article input/output
- [ ] AWS Integration
    - [ ] Uploading files to S3 bucket
    - [ ] Downlaoding files from S3 bucket
- [ ] Pre-processing
    - [ ] Cost estimation

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- contact -->
Andromeda Sawtelle - asawtelle@geneseo.edu

<p align="right">(<a href="#readme-top">back to top</a>)</p>



<!-- links -->
[Go-url]: https://go.dev
[Go.dev]: https://img.shields.io/badge/Go-00ADD8?logo=go&logoColor=white&style=for-the-badge
[Chi-url]: https://github.com/go-chi/chi
[Chi.com]: https://img.shields.io/badge/Chi-7C3AED?style=for-the-badge&logo=data:image/svg+xml;base64,PHN2ZyByb2xlPSJpbWciIHZpZXdCb3g9IjAgMCAyNCAyNCIgeG1sbnM9Imh0dHA6Ly93d3cudzMub3JnLzIwMDAvc3ZnIj48cGF0aCBmaWxsPSJ3aGl0ZSIgZD0iTTQuOTMgMi45M2ExIDEgMCAwIDEgMS40MSAwTDEyIDguNTlsNS42Ni01LjY2YTEgMSAwIDEgMSAxLjQxIDEuNDFMMTMuNDEgMTBsNS42NiA1LjY2YTEgMSAwIDAgMS0xLjQxIDEuNDFMMTIgMTEuNDFsLTUuNjYgNS42NmExIDEgMCAwIDEtMS40MS0xLjQxTDEwLjU5IDEwIDQuOTMgNC4zNGExIDEgMCAwIDEgMC0xLjQxeiIvPjwvc3ZnPg%3D%3D
[Pgx-url]: https://github.com/jackc/pgx
[Pgx.com]: https://img.shields.io/badge/Pgx-336791?style=for-the-badge&logo=postgresql&logoColor=white
[Openapi.com]: https://img.shields.io/badge/OpenAPI-6BA539?logo=openapiinitiative&logoColor=white&style=for-the-badge
